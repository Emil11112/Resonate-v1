from flask import Flask, render_template, request, redirect, url_for, flash, session
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
import os
import json
from datetime import datetime, timedelta
import uuid  
import spotipy
from spotipy.oauth2 import SpotifyOAuth
from dotenv import load_dotenv

#Hämtar variabler från .env filen
load_dotenv()

#Konfigurerar Spotifys API
SPOTIFY_CLIENT_ID = os.getenv('SPOTIFY_CLIENT_ID')
SPOTIFY_CLIENT_SECRET = os.getenv('SPOTIFY_CLIENT_SECRET')
SPOTIFY_REDIRECT_URI = 'http://localhost:5000/spotify/callback'

# Startar flask-appen
app = Flask(__name__)
app.config['SECRET_KEY'] = '1234567812312'  # Slängde in lite random siffror som blir vår client-secret
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///users.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB max-limit

# File upload konfiguration
UPLOAD_FOLDER = 'static'
PROFILE_PICS_FOLDER = os.path.join(UPLOAD_FOLDER, 'profile_pics')
SONG_PICS_FOLDER = os.path.join(UPLOAD_FOLDER, 'song_pics')
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif'}

# Spotify OAuth konfig
SPOTIFY_SCOPES = [
    'user-read-private',  # Basic user info
    'user-read-email',    # Email access
    'user-top-read',      # Top tracks/artists
    'user-library-read',  # User's saved tracks
    'playlist-read-private'  # User's playlists
]

# Vi kollar att uppladdnings konfigen finns
os.makedirs(PROFILE_PICS_FOLDER, exist_ok=True)
os.makedirs(SONG_PICS_FOLDER, exist_ok=True)

# Funktion som kollar om filen är i korrekt format
def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

# startar SQLAlchemy
db = SQLAlchemy(app)

# startar Flask-Login
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'

 # Här under så definierar vi databasen

 # Först ut så är tabellen för followers, med ID för den som följer och blir följd.
followers = db.Table('followers',
    db.Column('followerId', db.String(36), db.ForeignKey('users.userId'), primary_key=True),
    db.Column('followingId', db.String(36), db.ForeignKey('users.userId'), primary_key=True)
)

# Allt som sparas i databasen för en User
class User(UserMixin, db.Model):
    __tablename__ = 'users'
    userId = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    username = db.Column(db.String(80), nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password = db.Column(db.String(128))
    profilePicture = db.Column(db.String, nullable=True, default='default.jpg')
    favoriteGenres = db.Column(db.String, nullable=True)
    createdAt = db.Column(db.DateTime, default=datetime.utcnow)
    spotify_access_token = db.Column(db.String(255), nullable=True)
    spotify_refresh_token = db.Column(db.String(255), nullable=True)
    spotify_user_id = db.Column(db.String(255), nullable=True)
    spotify_token_expiry = db.Column(db.DateTime, nullable=True)
    bio = db.Column(db.Text, nullable=True)
    sotd_title = db.Column(db.String(200), nullable=True)
    sotd_artist = db.Column(db.String(200), nullable=True)
    song_picture = db.Column(db.String(200), nullable=True)
    favorite_songs = db.Column(db.String, nullable=True)

    # Followers relationen
    _followers = db.relationship(
        'User', 
        secondary='followers',
        primaryjoin='User.userId==followers.c.followerId',
        secondaryjoin='User.userId==followers.c.followingId',
        backref='following'
    )

    # Funktionen ska returnera vilka som följer en
    def followers(self):
        
        return db.session.query(User).join(
            followers, 
            (followers.c.followerId == User.userId)
        ).filter(followers.c.followingId == self.userId)
    
    # Hämtar id:et för en användare
    def get_id(self):
        return self.userId


    def __init__(self, username, email, password=None, **kwargs):
        # ETT UUID genereras om inte det finns
        self.userId = kwargs.get('userId', str(uuid.uuid4()))
        
        #Grundläggande information tilldelas och om ett lösen finns så hashas det.
        self.username = username
        self.email = email
        if password:
            self.set_password(password)
        
        # Här kollar vi på attributen som skickats med och lägger till de som redan finns i klassen
        for key, value in kwargs.items():
            if hasattr(self, key):
                setattr(self, key, value)
    
    # Dessa är ganska självklara
    def set_password(self, password):
        self.password = generate_password_hash(password)
        
    def check_password(self, password):
        return check_password_hash(self.password, password)
    
    def follow(self, user):
        if not self.is_following(user):
            self.followed.append(user)
            
    def unfollow(self, user):
        if self.is_following(user):
            self.followed.remove(user)

    def is_following(self, user):
        return db.session.query(followers).filter(
        followers.c.followerId == self.userId,
        followers.c.followingId == user.userId
        ).count() > 0
    
    # Tabell i databasen för posts
class Post(db.Model):
    __tablename__ = 'posts'
    postId = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    userId = db.Column(db.String(36), db.ForeignKey('users.userId'), nullable=False)
    content = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    # Relationen med user
    user = db.relationship('User', backref=db.backref('posts', lazy='dynamic'))
    
    # Relation med likes och comments
    likes = db.relationship('Like', primaryjoin='Post.postId==Like.postId', 
                            backref='post', lazy='dynamic')
    comments = db.relationship('Comment', primaryjoin='Post.postId==Comment.postId', 
                               backref='post', lazy='dynamic')

# Tabell för likes
class Like(db.Model):
    __tablename__ = 'likes'
    likeId = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    userId = db.Column(db.String(36), db.ForeignKey('users.userId'), nullable=False)
    postId = db.Column(db.String(36), db.ForeignKey('posts.postId'), nullable=False)
    
    # Relation med user
    user = db.relationship('User', backref='likes')

# Tabell för kommentarer
class Comment(db.Model):
    __tablename__ = 'comments'
    commentId = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    userId = db.Column(db.String(36), db.ForeignKey('users.userId'), nullable=False)
    postId = db.Column(db.String(36), db.ForeignKey('posts.postId'), nullable=False)
    content = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    # Relation
    user = db.relationship('User', backref='comments')

# Lägger dynamiskt till nya metoder
def add_methods_to_user_model(User):
    # Om en användare gillar ett inlägg så kollar den upp i databasen efter userid, postid och om det hittas blir det en like, annars none.
    def has_liked_post(self, post):
        return Like.query.filter_by(userId=self.userId, postId=post.postId).first() is not None
    
    User.has_liked_post = has_liked_post
    return User

#Metod tillägget appliceras här
User = add_methods_to_user_model(User)
         
#Refreshar Spotifys token om den har gått ut. Returnerar en bool om den byttes succesfully.         
def refresh_spotify_token(user):
  
    if not user.spotify_refresh_token:
        return False
    
    try:
        sp_oauth = SpotifyOAuth(
            client_id=SPOTIFY_CLIENT_ID,
            client_secret=SPOTIFY_CLIENT_SECRET,
            redirect_uri=SPOTIFY_REDIRECT_URI,
            scope=' '.join(SPOTIFY_SCOPES)
        )
        
        # Försöker refresh:a token
        new_token = sp_oauth.refresh_access_token(user.spotify_refresh_token)
        
        # Uppdaterar användarens token information
        user.spotify_access_token = new_token['access_token']
        user.spotify_token_expiry = datetime.utcnow() + timedelta(seconds=new_token['expires_in'])
        
        if 'refresh_token' in new_token:
            # If a new refresh token is provided, update it
            user.spotify_refresh_token = new_token['refresh_token']
        
        db.session.commit()
        return True
    except Exception as e:
        print(f"Error refreshing Spotify token: {e}")
        return False





# Här så försöker vi konvertera en JSON-sträng till en lista, om det inte går blir det en tom lista.
@app.template_filter('load_json')
def load_json(value):
    try:
        return json.loads(value) if value else []
    except json.JSONDecodeError:
        return []
# Hämtar en användare baserat på user_id från databasen
@login_manager.user_loader
def load_user(user_id):
    return db.session.get(User, user_id)

# Route-handlers 
@app.route('/')
def index():
    # Här leds man till homepagen där vi har recent posts och man kan logga in
    if current_user.is_authenticated:
        # Hämtar posts från andra och sig själv
        followed_user_ids = [user.userId for user in current_user.following] + [current_user.userId]
        posts = Post.query.filter(Post.userId.in_(followed_user_ids)).order_by(Post.created_at.desc()).limit(10).all()
    else:
        # Om du inte är inloggad ser du posts från alla
        posts = Post.query.order_by(Post.created_at.desc()).limit(10).all()
    
    # Vi använder en dictionary för att koppla posts till användare
    posts_with_users = [
        {
            'post': post, 
            'user': db.session.get(User, post.userId)  
        } for post in posts
    ]
    
    return render_template('index.html', posts=posts_with_users)

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
        profile_pic = 'default.jpg'
        if 'profilePicture' in request.files:
            file = request.files['profilePicture']
            if file.filename != '':
                filename = secure_filename(file.filename)
                file.save(os.path.join(PROFILE_PICS_FOLDER, filename))
                profile_pic = filename
        
        # Create new user
        new_user = User(
            username=username, 
            email=email, 
            password=password,
            profilePicture=profile_pic, 
            favoriteGenres=favorite_genre
        )
        
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
        content = request.form.get('content')
        
        if not content:
            flash('Post content cannot be empty.')
            return redirect(url_for('create_post'))
        
        # Create new post
        new_post = Post(
            userId=current_user.userId,
            content=content
        )
        
        db.session.add(new_post)
        db.session.commit()
        
        flash('Post created successfully!')
        return redirect(url_for('view_post', post_id=new_post.postId))
    
    return render_template('create_post.html')

@app.route('/post/<post_id>')
def view_post(post_id):
    post = Post.query.get_or_404(post_id)
    return render_template('view_post.html', post=post, Comment=Comment, user=post.user)

@app.route('/post/<post_id>/like', methods=['POST'])
@login_required
def like_post(post_id):
    post = Post.query.get_or_404(post_id)
    
    # Check if user has already liked the post
    existing_like = Like.query.filter_by(
        userId=current_user.userId, 
        postId=post.postId
    ).first()
    
    if existing_like:
        # Unlike the post
        db.session.delete(existing_like)
        flash('Post unliked.')
    else:
        # Like the post
        new_like = Like(
            userId=current_user.userId,
            postId=post.postId
        )
        db.session.add(new_like)
        flash('Post liked.')
    
    db.session.commit()
    return redirect(url_for('view_post', post_id=post_id))

@app.route('/post/<post_id>/comment', methods=['POST'])
@login_required
def add_comment(post_id):
    post = Post.query.get_or_404(post_id)
    content = request.form.get('content')
    
    if not content:
        flash('Comment cannot be empty.')
        return redirect(url_for('view_post', post_id=post_id))
    
    new_comment = Comment(
        userId=current_user.userId,
        postId=post.postId,
        content=content
    )
    
    db.session.add(new_comment)
    db.session.commit()
    
    flash('Comment added successfully!')
    return redirect(url_for('view_post', post_id=post_id))

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
    
    # Get posts only from the specific user
    posts = Post.query.filter_by(userId=user.userId).order_by(Post.created_at.desc()).all()
    
    return render_template('profile.html', user=user, posts=posts)

@app.route('/edit_profile', methods=['GET', 'POST'])
@login_required
def edit_profile():
    if request.method == 'POST':
        # Process basic profile information
        current_user.email = request.form.get('email', current_user.email)
        current_user.favoriteGenres = request.form.get('favorite_genre', current_user.favoriteGenres)
        current_user.bio = request.form.get('bio', current_user.bio)
        
        # Process song of the day data
        current_user.sotd_title = request.form.get('sotd_title', '')
        current_user.sotd_artist = request.form.get('sotd_artist', '')
        
        # Process favorite songs
        favorite_songs = []
        for i in range(5):  # We have 5 song slots
            title = request.form.get(f'song_title_{i}')
            artist = request.form.get(f'song_artist_{i}')
            icon = request.form.get(f'song_icon_{i}')
            
            # Only add if both title and artist are provided
            if title and artist:
                favorite_songs.append({
                    'title': title,
                    'artist': artist,
                    'icon': icon or '🎵'  # Default icon if not provided
                })
        
        # Save favorite songs as JSON string
        current_user.favorite_songs = json.dumps(favorite_songs) if favorite_songs else None
        
        # Handle profile picture upload
        if 'profilePicture' in request.files:
            file = request.files['profilePicture']
            if file and file.filename != '':
                filename = secure_filename(f"{current_user.username}_{int(datetime.utcnow().timestamp())}_profile.{file.filename.rsplit('.', 1)[1].lower()}")
                file.save(os.path.join(PROFILE_PICS_FOLDER, filename))
                current_user.profilePicture = filename
        
        # Handle song picture upload
        if 'song_picture' in request.files:
            file = request.files['song_picture']
            if file and file.filename != '':
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
    
    # Check if already following
    if user not in current_user.following:
        current_user.following.append(user)
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
    
    # Check if currently following
    if user in current_user.following:
        current_user.following.remove(user)
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
        'profilePicture': user.profilePicture,
        'favorite_genre': user.favorite_genre,
        'bio': user.bio if hasattr(user, 'bio') else None,
        'song_picture': user.song_picture if hasattr(user, 'song_picture') else None,
        'sotd_title': user.sotd_title if hasattr(user, 'sotd_title') else None,
        'sotd_artist': user.sotd_artist if hasattr(user, 'sotd_artist') else None,
        'favorite_songs': user.favorite_songs if hasattr(user, 'favorite_songs') else []
    }
    
    return f"<pre>{json.dumps(user_data, indent=4)}</pre>"

@app.route('/spotify/connect')
@login_required
def spotify_connect():
    """Initiate Spotify OAuth connection"""
    sp_oauth = SpotifyOAuth(
        client_id=SPOTIFY_CLIENT_ID,
        client_secret=SPOTIFY_CLIENT_SECRET,
        redirect_uri='http://localhost:5000/spotify/callback',
        scope=' '.join(SPOTIFY_SCOPES),
        show_dialog=True
    )
    auth_url = sp_oauth.get_authorize_url()
    return redirect(auth_url)

@app.route('/spotify/callback')
@login_required
def spotify_callback():
    """Handle Spotify OAuth callback"""
    try:
        sp_oauth = SpotifyOAuth(
            client_id=SPOTIFY_CLIENT_ID,
            client_secret=SPOTIFY_CLIENT_SECRET,
            redirect_uri=SPOTIFY_REDIRECT_URI,
            scope=' '.join(SPOTIFY_SCOPES)
        )
        
        # Get the access token
        code = request.args.get('code')
        if not code:
            flash('No authorization code found.', 'error')
            return redirect(url_for('profile', username=current_user.username))

        # Use get_access_token with error handling
        token_info = sp_oauth.get_access_token(code, as_dict=True)
        
        if not token_info:
            flash('Failed to retrieve access token.', 'error')
            return redirect(url_for('profile', username=current_user.username))

        # Create Spotify client
        sp = spotipy.Spotify(auth=token_info['access_token'])
        
        # Attempt to get current user's Spotify profile
        spotify_user = sp.current_user()
        
        # Update user's Spotify information
        current_user.spotify_user_id = spotify_user['id']
        current_user.spotify_access_token = token_info['access_token']
        current_user.spotify_refresh_token = token_info.get('refresh_token')
        current_user.spotify_token_expiry = datetime.utcnow() + timedelta(seconds=token_info['expires_in'])
        
        db.session.commit()
        
        flash('Successfully connected to Spotify!', 'success')
        return redirect(url_for('profile', username=current_user.username))

    except Exception as e:
        # Log the full error for debugging
        print(f"Spotify Callback Error: {e}")
        import traceback
        print(traceback.format_exc())
        
        flash(f'An error occurred: {str(e)}', 'error')
        return redirect(url_for('profile', username=current_user.username))

@app.route('/spotify/disconnect')
@login_required
def spotify_disconnect():
    """Disconnect Spotify account"""
    current_user.spotify_access_token = None
    current_user.spotify_refresh_token = None
    current_user.spotify_user_id = None
    current_user.spotify_token_expiry = None
    
    db.session.commit()
    
    flash('Spotify account disconnected.')
    return redirect(url_for('profile', username=current_user.username))

@app.route('/spotify/sync_songs', methods=['POST'])
@login_required
def spotify_sync_songs():
    """Synchronize user's Spotify favorite songs to their profile"""
    # Check if Spotify is connected
    if not current_user.spotify_access_token:
        flash('Please connect your Spotify account first.', 'error')
        return redirect(url_for('edit_profile'))
    
    try:
        # Create Spotify client
        sp = spotipy.Spotify(auth=current_user.spotify_access_token)
        
        # Fetch user's top tracks
        top_tracks = sp.current_user_top_tracks(limit=5, time_range='medium_term')
        
        # Prepare favorite songs list
        favorite_songs = []
        for track in top_tracks['items']:
            favorite_songs.append({
                'title': track['name'],
                'artist': track['artists'][0]['name'],
                'spotify_id': track['id']
            })
        
        # Save to user profile
        current_user.favorite_songs = json.dumps(favorite_songs)
        db.session.commit()
        
        flash('Successfully synced Spotify songs!', 'success')
    except Exception as e:
        app.logger.error(f"Spotify song sync error: {str(e)}")
        flash('Failed to sync Spotify songs. Please try again.', 'error')
    
    return redirect(url_for('edit_profile'))

@app.context_processor
def spotify_context_processor():
    """
    Provide Spotify-related helper functions to templates
    """
    def get_spotify_top_tracks(user):
        """
        Retrieve user's top Spotify tracks
        """
        if not user.spotify_access_token:
            return []
        
        try:
            sp = spotipy.Spotify(auth=user.spotify_access_token)
            top_tracks = sp.current_user_top_tracks(limit=5, time_range='medium_term')
            
            formatted_tracks = []
            for track in top_tracks['items']:
                formatted_tracks.append({
                    'name': track['name'],
                    'artist': track['artists'][0]['name'],
                    'album_art': track['album']['images'][0]['url'] if track['album']['images'] else None,
                    'external_url': track['external_urls']['spotify']
                })
            
            return formatted_tracks
        except Exception as e:
            print(f"Error fetching top tracks: {e}")
            return []

    def get_spotify_playlists(user):
        """
        Retrieve user's Spotify playlists
        """
        if not user.spotify_access_token:
            return []
        
        try:
            sp = spotipy.Spotify(auth=user.spotify_access_token)
            playlists = sp.current_user_playlists(limit=6)
            
            formatted_playlists = []
            for playlist in playlists['items']:
                formatted_playlists.append({
                    'name': playlist['name'],
                    'tracks_count': playlist['tracks']['total'],
                    'external_url': playlist['external_urls']['spotify'],
                    'image_url': playlist['images'][0]['url'] if playlist['images'] else None
                })
            
            return formatted_playlists
        except Exception as e:
            print(f"Error fetching playlists: {e}")
            return []

    return dict(
        get_spotify_top_tracks=get_spotify_top_tracks,
        get_spotify_playlists=get_spotify_playlists
    )




# FÖR ATT KÖRA PROGRAMMET
if __name__ == '__main__':
    with app.app_context():
        db.create_all()
    app.run(debug=True)
