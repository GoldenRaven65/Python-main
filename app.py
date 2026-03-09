import os
from datetime import datetime

from flask import Flask, redirect, render_template, request, send_from_directory, url_for, flash
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)

# Vaste SECRET_KEY nodig zodat sessies geldig blijven na herstarts op Azure
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'wijzig-dit-naar-een-veilige-sleutel-in-productie')

# Database: op Azure App Service (Linux) is /home persistent
if os.environ.get('WEBSITE_HOSTNAME'):  # draait op Azure
    db_path = '/home/taskmanager.db'
else:
    db_path = os.path.join(os.path.abspath(os.path.dirname(__file__)), 'taskmanager.db')
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get('DATABASE_URL', f'sqlite:///{db_path}')

# Session cookie instellingen voor Azure (HTTPS)
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
    tasks = db.relationship('Task', backref='owner', lazy=True)

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
    status = db.Column(db.String(20), nullable=False, default='open')  # open, bezig, afgerond
    priority = db.Column(db.String(10), nullable=False, default='normaal')  # laag, normaal, hoog
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    due_date = db.Column(db.Date, nullable=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)


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
        query = Task.query.filter_by(user_id=current_user.id)

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
                    return render_template('task_form.html', action='create')

            task = Task(
                title=title,
                description=description,
                priority=priority,
                due_date=due_date,
                user_id=current_user.id,
            )
            db.session.add(task)
            db.session.commit()
            flash('Taak aangemaakt!', 'success')
            return redirect(url_for('tasks'))
    return render_template('task_form.html', action='create')


@app.route('/tasks/<int:task_id>/edit', methods=['GET', 'POST'])
@login_required
def task_edit(task_id):
    task = db.session.get(Task, task_id)
    if not task:
        flash('Taak niet gevonden.', 'danger')
        return redirect(url_for('tasks'))
    if not current_user.is_admin and task.user_id != current_user.id:
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
                    return render_template('task_form.html', action='edit', task=task)
            else:
                task.due_date = None
            db.session.commit()
            flash('Taak bijgewerkt!', 'success')
            return redirect(url_for('tasks'))
    return render_template('task_form.html', action='edit', task=task)


@app.route('/tasks/<int:task_id>/delete', methods=['POST'])
@login_required
def task_delete(task_id):
    task = db.session.get(Task, task_id)
    if not task:
        flash('Taak niet gevonden.', 'danger')
        return redirect(url_for('tasks'))
    if not current_user.is_admin and task.user_id != current_user.id:
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
    if not current_user.is_admin and task.user_id != current_user.id:
        flash('Geen toegang tot deze taak.', 'danger')
        return redirect(url_for('tasks'))
    if task.status == 'afgerond':
        task.status = 'open'
    else:
        task.status = 'afgerond'
    db.session.commit()
    return redirect(url_for('tasks'))


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
    db.create_all()
    # Maak een standaard admin-account als die nog niet bestaat
    if not User.query.filter_by(username='admin').first():
        admin = User(username='admin', email='admin@taskmanager.local', role='admin')
        admin.set_password('admin123')
        db.session.add(admin)
        db.session.commit()


if __name__ == '__main__':
    app.run(debug=True)
