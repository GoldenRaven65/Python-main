import os
from datetime import datetime

from flask import Flask, redirect, render_template, request, send_from_directory, url_for, flash
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
from sqlalchemy import or_
from werkzeug.security import generate_password_hash, check_password_hash
import psycopg2  # PostgreSQL adapter (gebruikt door SQLAlchemy bij postgresql:// DATABASE_URL)
from azure.keyvault.secrets import SecretClient
from azure.identity import DefaultAzureCredential

app = Flask(__name__)

# SECRET_KEY
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'wijzig-dit-naar-een-veilige-sleutel-in-productie')

# Azure Key Vault: haal database-wachtwoord op via beheerde identiteit
db_password = None
keyVaultName = os.environ.get('KEY_VAULT_NAME')
if keyVaultName:
    try:
        KVUri = f"https://{keyVaultName}.vault.azure.net"
        credential = DefaultAzureCredential()
        kv_client = SecretClient(vault_url=KVUri, credential=credential)
        db_password = kv_client.get_secret("SQLsecret").value
    except Exception as e:
        print(f"Key Vault kon niet worden bereikt: {e}")

# Database: bouw PostgreSQL-verbinding op voor Azure TaskmanagerDB,
# of gebruik DATABASE_URL env-var, anders val terug op lokale SQLite.
database_url = os.environ.get('DATABASE_URL', 'postgresql://martijnpython.postgres.database.azure.com')

if not database_url and db_password:
    #POSTGRES_USER op de database-gebruiker (standaard: MartijnWissenburg)
    postgres_server = os.environ.get('POSTGRES_SERVER', 'martijnpython')
    postgres_user = os.environ.get('POSTGRES_USER', 'MartijnWissenburg')
    if postgres_server:
        database_url = (
            f"postgresql://{postgres_user}:{db_password}"
            f"@{postgres_server}.postgres.database.azure.com:5432/TaskmanagerDB"
            f"?sslmode=require"
        )

if database_url:
    # Azure levert soms "postgres://" — SQLAlchemy vereist "postgresql://"
    if database_url.startswith('postgres://'):
        database_url = database_url.replace('postgres://', 'postgresql://', 1)
    app.config['SQLALCHEMY_DATABASE_URI'] = database_url
else:
    if os.environ.get('WEBSITE_HOSTNAME'):  # Azure zonder database-config: persistent /home
        db_path = '/home/taskmanager.db'
    else:
        db_path = os.path.join(os.path.abspath(os.path.dirname(__file__)), 'taskmanager.db')
    app.config['SQLALCHEMY_DATABASE_URI'] = f'sqlite:///{db_path}'

# Session cookie instellingen voor Azure
app.config['SESSION_COOKIE_SECURE'] = bool(os.environ.get('WEBSITE_HOSTNAME'))
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'

db = SQLAlchemy(app)
login_manager = LoginManager(app)
login_manager.login_view = 'login'
login_manager.login_message = 'Log eerst in om deze pagina te bekijken.'
login_manager.login_message_category = 'info'


# ──────────────── Models ────────────────

class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(256), nullable=False)
    role = db.Column(db.String(10), nullable=False, default='user')   # 'admin' of 'user'
    display_name = db.Column(db.String(100), nullable=True)
    bio = db.Column(db.Text, nullable=True)
    avatar_color = db.Column(db.String(7), nullable=False, default='#ee653f')
    tasks = db.relationship('Task', backref='owner', lazy=True, foreign_keys='Task.user_id')
    assigned_tasks = db.relationship('Task', backref='assignee', lazy=True, foreign_keys='Task.assigned_to_id')

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    @property
    def is_admin(self):
        return self.role == 'admin'


class Task(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text, default='')
    status = db.Column(db.String(20), nullable=False, default='open') 
    priority = db.Column(db.String(10), nullable=False, default='normaal') 
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    due_date = db.Column(db.Date, nullable=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    assigned_to_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)


@login_manager.user_loader
def load_user(user_id):
    return db.session.get(User, int(user_id))


# ──────────────── Auth routes ────────────────

@app.route('/')
def index():
    if current_user.is_authenticated:
        return redirect(url_for('tasks'))
    return redirect(url_for('login'))


@app.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('tasks'))
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')
        user = User.query.filter_by(username=username).first()
        if user and user.check_password(password):
            login_user(user)
            next_page = request.args.get('next')
            return redirect(next_page or url_for('tasks'))
        flash('Ongeldige gebruikersnaam of wachtwoord.', 'danger')
    return render_template('login.html')


@app.route('/register', methods=['GET', 'POST'])
def register():
    if current_user.is_authenticated:
        return redirect(url_for('tasks'))
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        email = request.form.get('email', '').strip()
        password = request.form.get('password', '')
        password2 = request.form.get('password2', '')

        if not username or not email or not password:
            flash('Vul alle velden in.', 'danger')
        elif password != password2:
            flash('Wachtwoorden komen niet overeen.', 'danger')
        elif len(password) < 6:
            flash('Wachtwoord moet minimaal 6 tekens zijn.', 'danger')
        elif User.query.filter_by(username=username).first():
            flash('Gebruikersnaam is al in gebruik.', 'danger')
        elif User.query.filter_by(email=email).first():
            flash('E-mailadres is al in gebruik.', 'danger')
        else:
            user = User(username=username, email=email, role='user')
            user.set_password(password)
            db.session.add(user)
            db.session.commit()
            flash('Account aangemaakt! Je kunt nu inloggen.', 'success')
            return redirect(url_for('login'))
    return render_template('register.html')


@app.route('/logout')
@login_required
def logout():
    logout_user()
    flash('Je bent uitgelogd.', 'info')
    return redirect(url_for('login'))


# ──────────────── Task routes ────────────────

@app.route('/tasks')
@login_required
def tasks():
    status_filter = request.args.get('status', '')
    if current_user.is_admin:
        query = Task.query
    else:
        query = Task.query.filter(or_(Task.user_id == current_user.id, Task.assigned_to_id == current_user.id))

    if status_filter:
        query = query.filter_by(status=status_filter)

    all_tasks = query.order_by(Task.created_at.desc()).all()
    return render_template('tasks.html', tasks=all_tasks, status_filter=status_filter)


@app.route('/tasks/create', methods=['GET', 'POST'])
@login_required
def task_create():
    if request.method == 'POST':
        title = request.form.get('title', '').strip()
        description = request.form.get('description', '').strip()
        priority = request.form.get('priority', 'normaal')
        due_date_str = request.form.get('due_date', '').strip()

        if not title:
            flash('Titel is verplicht.', 'danger')
        else:
            due_date = None
            if due_date_str:
                try:
                    due_date = datetime.strptime(due_date_str, '%Y-%m-%d').date()
                except ValueError:
                    flash('Ongeldige datum.', 'danger')
                    users = User.query.order_by(User.username).all() if current_user.is_admin else []
                    return render_template('task_form.html', action='create', users=users)

            assigned_to_id = None
            if current_user.is_admin:
                assign_val = request.form.get('assigned_to_id', '')
                if assign_val:
                    assigned_to_id = int(assign_val)

            task = Task(
                title=title,
                description=description,
                priority=priority,
                due_date=due_date,
                user_id=current_user.id,
                assigned_to_id=assigned_to_id,
            )
            db.session.add(task)
            db.session.commit()
            flash('Taak aangemaakt!', 'success')
            return redirect(url_for('tasks'))
    users = User.query.order_by(User.username).all() if current_user.is_admin else []
    return render_template('task_form.html', action='create', users=users)


@app.route('/tasks/<int:task_id>/edit', methods=['GET', 'POST'])
@login_required
def task_edit(task_id):
    task = db.session.get(Task, task_id)
    if not task:
        flash('Taak niet gevonden.', 'danger')
        return redirect(url_for('tasks'))
    if not current_user.is_admin and task.user_id != current_user.id and task.assigned_to_id != current_user.id:
        flash('Geen toegang tot deze taak.', 'danger')
        return redirect(url_for('tasks'))

    if request.method == 'POST':
        title = request.form.get('title', '').strip()
        if not title:
            flash('Titel is verplicht.', 'danger')
        else:
            task.title = title
            task.description = request.form.get('description', '').strip()
            task.status = request.form.get('status', task.status)
            task.priority = request.form.get('priority', task.priority)
            due_date_str = request.form.get('due_date', '').strip()
            if due_date_str:
                try:
                    task.due_date = datetime.strptime(due_date_str, '%Y-%m-%d').date()
                except ValueError:
                    flash('Ongeldige datum.', 'danger')
                    users = User.query.order_by(User.username).all() if current_user.is_admin else []
                    return render_template('task_form.html', action='edit', task=task, users=users)
            else:
                task.due_date = None
            if current_user.is_admin:
                assign_val = request.form.get('assigned_to_id', '')
                task.assigned_to_id = int(assign_val) if assign_val else None
            db.session.commit()
            flash('Taak bijgewerkt!', 'success')
            return redirect(url_for('tasks'))
    users = User.query.order_by(User.username).all() if current_user.is_admin else []
    return render_template('task_form.html', action='edit', task=task, users=users)


@app.route('/tasks/<int:task_id>/delete', methods=['POST'])
@login_required
def task_delete(task_id):
    task = db.session.get(Task, task_id)
    if not task:
        flash('Taak niet gevonden.', 'danger')
        return redirect(url_for('tasks'))
    if not current_user.is_admin and task.user_id != current_user.id and task.assigned_to_id != current_user.id:
        flash('Geen toegang tot deze taak.', 'danger')
        return redirect(url_for('tasks'))
    db.session.delete(task)
    db.session.commit()
    flash('Taak verwijderd.', 'success')
    return redirect(url_for('tasks'))


@app.route('/tasks/<int:task_id>/toggle', methods=['POST'])
@login_required
def task_toggle(task_id):
    task = db.session.get(Task, task_id)
    if not task:
        flash('Taak niet gevonden.', 'danger')
        return redirect(url_for('tasks'))
    if not current_user.is_admin and task.user_id != current_user.id and task.assigned_to_id != current_user.id:
        flash('Geen toegang tot deze taak.', 'danger')
        return redirect(url_for('tasks'))
    if task.status == 'afgerond':
        task.status = 'open'
    else:
        task.status = 'afgerond'
    db.session.commit()
    return redirect(url_for('tasks'))


# ──────────────── Profiel routes ────────────────

@app.route('/profile', methods=['GET', 'POST'])
@login_required
def profile():
    if request.method == 'POST':
        action = request.form.get('action')

        if action == 'update_profile':
            display_name = request.form.get('display_name', '').strip()
            bio = request.form.get('bio', '').strip()
            avatar_color = request.form.get('avatar_color', '#ee653f').strip()
            email = request.form.get('email', '').strip()

            if email and email != current_user.email:
                if User.query.filter_by(email=email).first():
                    flash('E-mailadres is al in gebruik.', 'danger')
                    return redirect(url_for('profile'))
                current_user.email = email

            current_user.display_name = display_name or None
            current_user.bio = bio or None
            current_user.avatar_color = avatar_color
            db.session.commit()
            flash('Profiel bijgewerkt!', 'success')

        elif action == 'change_password':
            current_pw = request.form.get('current_password', '')
            new_pw = request.form.get('new_password', '')
            new_pw2 = request.form.get('new_password2', '')

            if not current_user.check_password(current_pw):
                flash('Huidig wachtwoord is onjuist.', 'danger')
            elif len(new_pw) < 6:
                flash('Nieuw wachtwoord moet minimaal 6 tekens zijn.', 'danger')
            elif new_pw != new_pw2:
                flash('Wachtwoorden komen niet overeen.', 'danger')
            else:
                current_user.set_password(new_pw)
                db.session.commit()
                flash('Wachtwoord gewijzigd!', 'success')

        return redirect(url_for('profile'))

    total = len(current_user.tasks)
    done = sum(1 for t in current_user.tasks if t.status == 'afgerond')
    open_count = sum(1 for t in current_user.tasks if t.status == 'open')
    bezig_count = sum(1 for t in current_user.tasks if t.status == 'bezig')
    return render_template('profile.html', total=total, done=done, open_count=open_count, bezig_count=bezig_count)


# ──────────────── Admin routes ────────────────

@app.route('/admin')
@login_required
def admin_dashboard():
    if not current_user.is_admin:
        flash('Alleen admins hebben toegang.', 'danger')
        return redirect(url_for('tasks'))
    total_users = User.query.count()
    total_tasks = Task.query.count()
    open_tasks = Task.query.filter_by(status='open').count()
    bezig_tasks = Task.query.filter_by(status='bezig').count()
    done_tasks = Task.query.filter_by(status='afgerond').count()
    recent_tasks = Task.query.order_by(Task.created_at.desc()).limit(10).all()
    users = User.query.order_by(User.username).all()
    return render_template('admin_dashboard.html',
                           total_users=total_users, total_tasks=total_tasks,
                           open_tasks=open_tasks, bezig_tasks=bezig_tasks,
                           done_tasks=done_tasks, recent_tasks=recent_tasks,
                           users=users)


@app.route('/admin/users')
@login_required
def admin_users():
    if not current_user.is_admin:
        flash('Alleen admins hebben toegang.', 'danger')
        return redirect(url_for('tasks'))
    users = User.query.order_by(User.username).all()
    return render_template('admin_users.html', users=users)


@app.route('/admin/users/<int:user_id>/toggle-role', methods=['POST'])
@login_required
def admin_toggle_role(user_id):
    if not current_user.is_admin:
        flash('Alleen admins hebben toegang.', 'danger')
        return redirect(url_for('tasks'))
    user = db.session.get(User, user_id)
    if not user:
        flash('Gebruiker niet gevonden.', 'danger')
    elif user.id == current_user.id:
        flash('Je kunt je eigen rol niet wijzigen.', 'warning')
    else:
        user.role = 'user' if user.role == 'admin' else 'admin'
        db.session.commit()
        flash(f'Rol van {user.username} gewijzigd naar {user.role}.', 'success')
    return redirect(url_for('admin_users'))


@app.route('/users/<int:user_id>')
@login_required
def user_profile(user_id):
    user = db.session.get(User, user_id)
    if not user:
        flash('Gebruiker niet gevonden.', 'danger')
        return redirect(url_for('tasks'))
    total = len(user.tasks)
    done = sum(1 for t in user.tasks if t.status == 'afgerond')
    open_count = sum(1 for t in user.tasks if t.status == 'open')
    bezig_count = sum(1 for t in user.tasks if t.status == 'bezig')
    return render_template('user_profile.html', viewed_user=user,
                           total=total, done=done, open_count=open_count, bezig_count=bezig_count)


@app.route('/admin/users/<int:user_id>/edit', methods=['GET', 'POST'])
@login_required
def admin_edit_user(user_id):
    if not current_user.is_admin:
        flash('Alleen admins hebben toegang.', 'danger')
        return redirect(url_for('tasks'))
    user = db.session.get(User, user_id)
    if not user:
        flash('Gebruiker niet gevonden.', 'danger')
        return redirect(url_for('admin_users'))

    if request.method == 'POST':
        action = request.form.get('action')

        if action == 'update_profile':
            display_name = request.form.get('display_name', '').strip()
            bio = request.form.get('bio', '').strip()
            avatar_color = request.form.get('avatar_color', '#ee653f').strip()
            email = request.form.get('email', '').strip()
            username = request.form.get('username', '').strip()

            if not username:
                flash('Gebruikersnaam mag niet leeg zijn.', 'danger')
                return redirect(url_for('admin_edit_user', user_id=user_id))
            if username != user.username and User.query.filter_by(username=username).first():
                flash('Gebruikersnaam is al in gebruik.', 'danger')
                return redirect(url_for('admin_edit_user', user_id=user_id))
            if email and email != user.email and User.query.filter_by(email=email).first():
                flash('E-mailadres is al in gebruik.', 'danger')
                return redirect(url_for('admin_edit_user', user_id=user_id))

            user.username = username
            user.email = email or user.email
            user.display_name = display_name or None
            user.bio = bio or None
            user.avatar_color = avatar_color
            db.session.commit()
            flash(f'Profiel van {user.username} bijgewerkt.', 'success')

        elif action == 'reset_password':
            new_pw = request.form.get('new_password', '')
            new_pw2 = request.form.get('new_password2', '')
            if len(new_pw) < 6:
                flash('Wachtwoord moet minimaal 6 tekens zijn.', 'danger')
            elif new_pw != new_pw2:
                flash('Wachtwoorden komen niet overeen.', 'danger')
            else:
                user.set_password(new_pw)
                db.session.commit()
                flash(f'Wachtwoord van {user.username} gewijzigd.', 'success')

        return redirect(url_for('admin_edit_user', user_id=user_id))

    return render_template('admin_edit_user.html', edited_user=user)


@app.route('/admin/users/<int:user_id>/delete', methods=['POST'])
@login_required
def admin_delete_user(user_id):
    if not current_user.is_admin:
        flash('Alleen admins hebben toegang.', 'danger')
        return redirect(url_for('tasks'))
    user = db.session.get(User, user_id)
    if not user:
        flash('Gebruiker niet gevonden.', 'danger')
    elif user.id == current_user.id:
        flash('Je kunt jezelf niet verwijderen.', 'warning')
    else:
        Task.query.filter_by(user_id=user.id).delete()
        Task.query.filter_by(assigned_to_id=user.id).update({'assigned_to_id': None})
        db.session.delete(user)
        db.session.commit()
        flash(f'Gebruiker {user.username} verwijderd.', 'success')
    return redirect(url_for('admin_users'))


# ──────────────── Static ────────────────

@app.route('/favicon.ico')
def favicon():
    return send_from_directory(os.path.join(app.root_path, 'static'),
                               'favicon.ico', mimetype='image/vnd.microsoft.icon')


# ──────────────── Database init ────────────────

with app.app_context():
    db_uri = app.config['SQLALCHEMY_DATABASE_URI']
    if 'postgresql' in db_uri:
        # Verberg wachtwoord in logoutput
        safe_uri = db_uri.split('@')[-1] if '@' in db_uri else db_uri
        print(f"[DB] Verbinding met PostgreSQL: {safe_uri}")
    else:
        print(f"[DB] Lokale SQLite database actief")

    try:
        db.create_all()
        print("[DB] Tabellen aangemaakt / al aanwezig")
    except Exception as e:
        print(f"[DB] Fout bij db.create_all(): {e}")
        raise

    # Maak een standaard admin-account als die nog niet bestaat
    try:
        if not User.query.filter_by(username='admin').first():
            admin = User(username='admin', email='admin@taskmanager.local', role='admin')
            admin.set_password('admin123')
            db.session.add(admin)
            db.session.commit()
            print("[DB] Standaard admin-account aangemaakt")
    except Exception as e:
        print(f"[DB] Fout bij aanmaken admin-account: {e}")
        db.session.rollback()


if __name__ == '__main__':
    app.run(debug=True)
