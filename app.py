from flask import Flask, render_template, request, redirect, url_for, flash, session
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
import os
import json
from datetime import datetime

# Initialize Flask app
app = Flask(__name__)
app.config['SECRET_KEY'] = '1234567812312'  # Change this to a random secret key
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///users.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB max-limit

# File upload configurations
UPLOAD_FOLDER = 'static'
PROFILE_PICS_FOLDER = os.path.join(UPLOAD_FOLDER, 'profile_pics')
SONG_PICS_FOLDER = os.path.join(UPLOAD_FOLDER, 'song_pics')
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif'}

# Make sure the upload folders exist
os.makedirs(PROFILE_PICS_FOLDER, exist_ok=True)
os.makedirs(SONG_PICS_FOLDER, exist_ok=True)

# Function to check if file extension is allowed
def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

# Initialize SQLAlchemy
db = SQLAlchemy(app)

# Initialize Flask-Login
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'

# Define the followers association table
followers = db.Table('followers',
    db.Column('follower_id', db.Integer, db.ForeignKey('user.id')),
    db.Column('followed_id', db.Integer, db.ForeignKey('user.id'))
)

# User Model - with all columns
# User Model - simplified version
class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(128))
    profile_picture = db.Column(db.String(120), default='default.jpg')
    favorite_genre = db.Column(db.String(50), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    # Define the relationship with followers
    followed = db.relationship(
        'User', secondary=followers,
        primaryjoin=(followers.c.follower_id == id),
        secondaryjoin=(followers.c.followed_id == id),
        backref=db.backref('followers', lazy='dynamic'), lazy='dynamic')
    
    def set_password(self, password):
        self.password_hash = generate_password_hash(password)
        
    def check_password(self, password):
        return check_password_hash(self.password_hash, password)
    
    def follow(self, user):
        if not self.is_following(user):
            self.followed.append(user)
            
    def unfollow(self, user):
        if self.is_following(user):
            self.followed.remove(user)

    def is_following(self, user):
        return self.followed.filter(followers.c.followed_id == user.id).count() > 0

    def __repr__(self):
        return f'<User {self.username}>'

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

# Routes
@app.route('/')
def index():
    users = User.query.all()
    return render_template('index.html', users=users)

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form.get('username')
        email = request.form.get('email')
        password = request.form.get('password')
        favorite_genre = request.form.get('favorite_genre')

        # Check if username or email already exists
        user_exists = User.query.filter_by(username=username).first()
        email_exists = User.query.filter_by(email=email).first()
        
        if user_exists:
            flash('Username already exists.')
            return redirect(url_for('register'))
        
        if email_exists:
            flash('Email already exists.')
            return redirect(url_for('register'))
        
        # Handle profile picture upload
        if 'profile_picture' in request.files:
            file = request.files['profile_picture']
            if file.filename != '':
                filename = secure_filename(file.filename)
                file.save(os.path.join(PROFILE_PICS_FOLDER, filename))
                profile_pic = filename
            else:
                profile_pic = 'default.jpg'
        else:
            profile_pic = 'default.jpg'
        
        # Create new user
        new_user = User(username=username, email=email, profile_picture=profile_pic, favorite_genre=favorite_genre)
        new_user.set_password(password)
        
        # Add user to database
        db.session.add(new_user)
        db.session.commit()
        
        flash('Registration successful! Please log in.')
        return redirect(url_for('login'))
    
    return render_template('register.html')

@app.route('/create_post', methods=['GET', 'POST'])
@login_required
def create_post():
    if request.method == 'POST':
        # This will be implemented later
        flash('Post creation not implemented yet')
        return redirect(url_for('profile', username=current_user.username))
    
    return render_template('create_post.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        
        user = User.query.filter_by(username=username).first()
        
        if user and user.check_password(password):
            login_user(user)
            return redirect(url_for('profile', username=user.username))
        else:
            flash('Invalid username or password')
    
    return render_template('login.html')

@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('index'))

@app.route('/profile/<username>')
def profile(username):
    user = User.query.filter_by(username=username).first_or_404()
    return render_template('profile.html', user=user)

@app.route('/edit_profile', methods=['GET', 'POST'])
@login_required
def edit_profile():
    if request.method == 'POST':
        # Process form data
        current_user.email = request.form['email']
        current_user.favorite_genre = request.form['favorite_genre']
        current_user.bio = request.form['bio']
        
        # Process song of the day data
        current_user.sotd_title = request.form['sotd_title']
        current_user.sotd_artist = request.form['sotd_artist']
        
        # Process favorite songs
        favorite_songs = []
        for i in range(5):  # We have 5 song slots
            title = request.form.get(f'song_title_{i}')
            artist = request.form.get(f'song_artist_{i}')
            icon = request.form.get(f'song_icon_{i}')
            
            if title and artist:  # Only add if both title and artist are provided
                favorite_songs.append({
                    'title': title,
                    'artist': artist,
                    'icon': icon
                })
        
        current_user.favorite_songs = favorite_songs
        
        # Handle profile picture upload
        if 'profile_picture' in request.files and request.files['profile_picture'].filename:
            file = request.files['profile_picture']
            if file and allowed_file(file.filename):
                filename = secure_filename(f"{current_user.username}_{int(datetime.utcnow().timestamp())}_profile.{file.filename.rsplit('.', 1)[1].lower()}")
                file.save(os.path.join(PROFILE_PICS_FOLDER, filename))
                current_user.profile_picture = filename
        
        # Handle song picture upload
        if 'song_picture' in request.files and request.files['song_picture'].filename:
            file = request.files['song_picture']
            if file and allowed_file(file.filename):
                filename = secure_filename(f"{current_user.username}_{int(datetime.utcnow().timestamp())}_song.{file.filename.rsplit('.', 1)[1].lower()}")
                file.save(os.path.join(SONG_PICS_FOLDER, filename))
                current_user.song_picture = filename
        
        # Save changes to database
        db.session.commit()
        flash('Your profile has been updated!')
        return redirect(url_for('profile', username=current_user.username))
    
    return render_template('edit_profile.html')

@app.route('/follow/<username>')
@login_required
def follow(username):
    user = User.query.filter_by(username=username).first()
    if user is None:
        flash(f'User {username} not found.')
        return redirect(url_for('index'))
    if user == current_user:
        flash('You cannot follow yourself!')
        return redirect(url_for('profile', username=username))
    
    current_user.follow(user)
    db.session.commit()
    flash(f'You are now following {username}!')
    return redirect(url_for('profile', username=username))

@app.route('/unfollow/<username>')
@login_required
def unfollow(username):
    user = User.query.filter_by(username=username).first()
    if user is None:
        flash(f'User {username} not found.')
        return redirect(url_for('index'))
    if user == current_user:
        flash('You cannot unfollow yourself!')
        return redirect(url_for('profile', username=username))
    
    current_user.unfollow(user)
    db.session.commit()
    flash(f'You have unfollowed {username}.')
    return redirect(url_for('profile', username=username))

@app.route('/users')
def users():
    users = User.query.all()
    return render_template('users.html', users=users)

@app.route('/debug/<username>')
def debug_user(username):
    """Debug route to check user data"""
    user = User.query.filter_by(username=username).first_or_404()
    
    # Get all attributes of the user object
    user_data = {
        'username': user.username,
        'email': user.email,
        'profile_picture': user.profile_picture,
        'favorite_genre': user.favorite_genre,
        'bio': user.bio if hasattr(user, 'bio') else None,
        'song_picture': user.song_picture if hasattr(user, 'song_picture') else None,
        'sotd_title': user.sotd_title if hasattr(user, 'sotd_title') else None,
        'sotd_artist': user.sotd_artist if hasattr(user, 'sotd_artist') else None,
        'favorite_songs': user.favorite_songs if hasattr(user, 'favorite_songs') else []
    }
    
    return f"<pre>{json.dumps(user_data, indent=4)}</pre>"

if __name__ == '__main__':
    with app.app_context():
        db.create_all()
    app.run(debug=True)