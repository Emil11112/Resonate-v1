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
POST_PICS_FOLDER = os.path.join(UPLOAD_FOLDER, 'post_pics')
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
os.makedirs(POST_PICS_FOLDER, exist_ok=True)

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
    db.Column('follower_id', db.String(36), db.ForeignKey('users.userId'), primary_key=True),
    db.Column('followed_id', db.String(36), db.ForeignKey('users.userId'), primary_key=True)
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

    # Followers relationen med mer explicit metod
    followed = db.relationship(
        'User', 
        secondary=followers,
        primaryjoin=(followers.c.follower_id == userId),
        secondaryjoin=(followers.c.followed_id == userId),
        backref=db.backref('followers', lazy='dynamic'),
        lazy='dynamic'
    )

    # Lägg till following som en property för kompatibilitet
    @property
    def following(self):
        return self.followed

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
        """Follow another user."""
        if not self.is_following(user):
            self.followed.append(user)
            
    def unfollow(self, user):
        """Unfollow another user."""
        if self.is_following(user):
            self.followed.remove(user)

    def is_following(self, user):
        """Check if current user is following another user."""
        return self.followed.filter(followers.c.followed_id == user.userId).count() > 0
    
    def followers_count(self):
        """Get the number of followers."""
        return self.followers.count()

    
    # Tabell i databasen för posts
class Post(db.Model):
    __tablename__ = 'posts'
    postId = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    userId = db.Column(db.String(36), db.ForeignKey('users.userId'), nullable=False)
    content = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    post_picture = db.Column(db.String(200), nullable=True) 
    
    user = db.relationship('User', backref=db.backref('posts', lazy='dynamic'))
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

#Hanterar registrationen
@app.route('/register', methods=['GET', 'POST'])
# Posts kollar och processerar datan som vi har fått, get ger ett formulär för användaren att mata in.
def register():
    if request.method == 'POST':
        username = request.form.get('username')
        email = request.form.get('email')
        password = request.form.get('password')
        favorite_genre = request.form.get('favorite_genre')

        # Vi kollar om användarnamnet eller mailen redan brukas någonstans
        user_exists = User.query.filter_by(username=username).first()
        email_exists = User.query.filter_by(email=email).first()
        
        if user_exists:
            flash('Username already exists.')
            return redirect(url_for('register'))
        
        if email_exists:
            flash('Email already exists.')
            return redirect(url_for('register'))
        
        # Hanterar profilbilden. Använder default.jpg om det inte finns någon annan.
        profile_pic = 'default.jpg'
        if 'profilePicture' in request.files:
            file = request.files['profilePicture']
            if file.filename != '':
                filename = secure_filename(file.filename)
                file.save(os.path.join(PROFILE_PICS_FOLDER, filename))
                profile_pic = filename
        
        # Vi skapar en ny användare
        new_user = User(
            username=username, 
            email=email, 
            password=password,
            profilePicture=profile_pic, 
            favoriteGenres=favorite_genre
        )
        
        # Lägger till användaren till databasen
        db.session.add(new_user)
        db.session.commit()
        
        # Flash-notis som dyker upp lite snabbt bara på sidan
        flash('Registration successful! Please log in.')
        return redirect(url_for('login'))
    
    return render_template('register.html')

# En route för att skapa en post
@app.route('/create_post', methods=['GET', 'POST'])
@login_required
def create_post():
    if request.method == 'POST':
        # Get the content
        content = request.form.get('content')
        
        # If no content, return error
        if not content and 'post_picture' not in request.files:
            flash('Post must have content or an image.')
            return redirect(url_for('create_post'))
        
        # Handle image upload
        post_picture = None
        if 'post_picture' in request.files:
            file = request.files['post_picture']
            if file and file.filename != '':
                # Generate a unique filename
                filename = secure_filename(f"{current_user.username}_{int(datetime.utcnow().timestamp())}_post.{file.filename.rsplit('.', 1)[1].lower()}")
                # Save the file
                file.save(os.path.join(POST_PICS_FOLDER, filename))
                post_picture = filename
        
        # Create the post
        new_post = Post(
            userId=current_user.userId,
            content=content,
            post_picture=post_picture
        )
        
        # Add and commit to database
        db.session.add(new_post)
        db.session.commit()
        
        flash('Post created successfully!')
        return redirect(url_for('view_post', post_id=new_post.postId))
    
    return render_template('create_post.html')

# Route för att hantera post id och visa dess detaljer 
@app.route('/post/<post_id>')
def view_post(post_id):
    post = Post.query.get_or_404(post_id)
    # Om sidan hittas så returneras det, annars skapar den en 404-sida att det inte fanns
    return render_template('view_post.html', post=post, Comment=Comment, user=post.user)

#Hanterar hur likes funkar
@app.route('/post/<post_id>/like', methods=['POST'])
@login_required
def like_post(post_id):
    #Försöker hitta sidan annars blir det 404 sida
    post = Post.query.get_or_404(post_id)
    
    # Kollar så inte den redan är gillad
    existing_like = Like.query.filter_by(
        userId=current_user.userId, 
        postId=post.postId
    ).first()
    
    if existing_like:
        # Unlike:a posten
        db.session.delete(existing_like)
        # Notis
        flash('Post unliked.')
    else:
        # Like:a posten
        new_like = Like(
            userId=current_user.userId,
            postId=post.postId
        )
        #Commitar till databasen
        db.session.add(new_like)
        flash('Post liked.')
    
    db.session.commit()
    return redirect(url_for('view_post', post_id=post_id))

#Hanterar kommentarer, väldigt snarlik likes. 
@app.route('/post/<post_id>/comment', methods=['POST'])
@login_required
def add_comment(post_id):
    #Samma som för likes
    post = Post.query.get_or_404(post_id)
    content = request.form.get('content')
    
    # Om användaren inte skrev in något i formuläret som skickades ovan så kan det inte publiceras.
    if not content:
        flash('Comment cannot be empty.')
        return redirect(url_for('view_post', post_id=post_id))
    
    #Skapar kommentaren
    new_comment = Comment(
        userId=current_user.userId,
        postId=post.postId,
        content=content
    )
    
    db.session.add(new_comment)
    db.session.commit()
    
    flash('Comment added successfully!')
    return redirect(url_for('view_post', post_id=post_id))

#Hanterar hur man loggar in
@app.route('/login', methods=['GET', 'POST'])
def login():
    # Här frågar vi efter username och lösen
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        
        #Försöker hitta användarenamnet i databasen
        user = User.query.filter_by(username=username).first()
        
        #Om användaren finns och lösenordet stämmer med det sparade så returneras du till profilsidan, annars felmeddelande
        if user and user.check_password(password):
            login_user(user)
            return redirect(url_for('profile', username=user.username))
        else:
            flash('Invalid username or password')
    
    return render_template('login.html')

# Här kan man logga ut
@app.route('/logout')
@login_required
def logout():
    # Hänvisar till logout_user funktionen
    logout_user()
    return redirect(url_for('index'))

#För användarens profilsida
@app.route('/profile/<username>')
def profile(username):
    #Försöker hitta användaren eller 404-sida att det inte fanns. 
    user = User.query.filter_by(username=username).first_or_404()
    
    # Här hämtar vi alla posts från bara användaren
    posts = Post.query.filter_by(userId=user.userId).order_by(Post.created_at.desc()).all()
    
    return render_template('profile.html', user=user, posts=posts)

#För att redigera profilen
@app.route('/edit_profile', methods=['GET', 'POST'])
@login_required
def edit_profile():
    if request.method == 'POST':
        # Uppdatera grundläggande profilinformation
        current_user.email = request.form.get('email', current_user.email)
        current_user.favoriteGenres = request.form.get('favorite_genre', current_user.favoriteGenres)
        current_user.bio = request.form.get('bio', current_user.bio)
        
        # Uppdatera Song of the Day
        current_user.sotd_title = request.form.get('sotd_title', '')
        current_user.sotd_artist = request.form.get('sotd_artist', '')
        
        # Hantera favoritlåtar
        favorite_songs = []
        for i in range(5):  # 5 låtplatser
            title = request.form.get(f'song_title_{i}')
            artist = request.form.get(f'song_artist_{i}')
            icon = request.form.get(f'song_icon_{i}')
            
            # Lägg bara till om både titel och artist finns
            if title and artist:
                favorite_songs.append({
                    'title': title,
                    'artist': artist,
                    'icon': icon or '🎵'  # Vi har en default emoji om inget annat uppges
                })
        
        # Spara favoritlåtar som JSON-sträng
        current_user.favorite_songs = json.dumps(favorite_songs) if favorite_songs else None
        
        # Hantera profilbild
        if 'profilePicture' in request.files:
            file = request.files['profilePicture']
            if file and file.filename != '':
                filename = secure_filename(f"{current_user.username}_{int(datetime.utcnow().timestamp())}_profile.{file.filename.rsplit('.', 1)[1].lower()}")
                file.save(os.path.join(PROFILE_PICS_FOLDER, filename))
                current_user.profilePicture = filename
        
        # Hantera låt-bild
        if 'song_picture' in request.files:
            file = request.files['song_picture']
            if file and file.filename != '':
                filename = secure_filename(f"{current_user.username}_{int(datetime.utcnow().timestamp())}_song.{file.filename.rsplit('.', 1)[1].lower()}")
                file.save(os.path.join(SONG_PICS_FOLDER, filename))
                current_user.song_picture = filename
        
        # Spotify-synkronisering
        if current_user.spotify_access_token:
            try:
                # Skapa Spotify-klient
                sp = spotipy.Spotify(auth=current_user.spotify_access_token)
                
                # Hämta topplåtar direkt via Spotify API
                top_tracks = sp.current_user_top_tracks(limit=5, time_range='medium_term')
                
                spotify_favorite_songs = []
                for track in top_tracks['items']:
                    spotify_favorite_songs.append({
                        'title': track['name'],
                        'artist': track['artists'][0]['name'],
                        'icon': User._get_track_emoji(track['name']),
                        'spotify_id': track['id']
                    })
                
                # Valfri: Lägg till Spotify-låtar om inga manuellt valts
                if not favorite_songs and spotify_favorite_songs:
                    current_user.favorite_songs = json.dumps(spotify_favorite_songs)
            except Exception as e:
                # Felhantering om Spotify-synk misslyckas
                print(f"Spotify sync error: {e}")
        
        # Spara ändringar
        db.session.commit()
        
        flash('Your profile has been updated!')
        return redirect(url_for('profile', username=current_user.username))
    
    return render_template('edit_profile.html')

# Hur man följer en användare
@app.route('/follow/<username>')
@login_required
def follow(username):
    #Försöker hämta användaren i databasen
    user = User.query.filter_by(username=username).first()
    #Om den inte finns så ska det bli felmeddelande
    if user is None:
        flash(f'User {username} not found.')
        return redirect(url_for('index'))
    # Du ska inte kunna följa dig själv heller
    if user == current_user:
        flash('You cannot follow yourself!')
        return redirect(url_for('profile', username=username))
    
    # Om du inte redan följer människan så gör du det nu och uppdaterar databasen
    if not current_user.is_following(user):
        current_user.follow(user)
        db.session.commit()
        flash(f'You are now following {username}!')
    
    return redirect(url_for('profile', username=username))

#Avföljer ett konto på snarlikt sätt
@app.route('/unfollow/<username>')
@login_required
def unfollow(username):
    #Försöker hitta användaren och kolla så att den finns och inte är du
    user = User.query.filter_by(username=username).first()
    if user is None:
        flash(f'User {username} not found.')
        return redirect(url_for('index'))
    if user == current_user:
        flash('You cannot unfollow yourself!')
        return redirect(url_for('profile', username=username))
    
    # kollar om du följer människan, annars görs inget.
    if current_user.is_following(user):
        current_user.unfollow(user)
        db.session.commit()
        flash(f'You have unfollowed {username}.')
    
    return redirect(url_for('profile', username=username))

# Här hämtar vi datan för ALLA människor och skickar till Users-sidan där man kan se alla konton.
@app.route('/users')
def users():
    users = User.query.all()
    return render_template('users.html', users=users)

# Här under har vi allt med vår Spotify koppling
@app.route('/spotify/connect')
@login_required
def spotify_connect():
    """Startar Spotify OAuth connection"""
    sp_oauth = SpotifyOAuth(
        client_id=SPOTIFY_CLIENT_ID,
        client_secret=SPOTIFY_CLIENT_SECRET,
        redirect_uri='http://localhost:5000/spotify/callback',
        scope=' '.join(SPOTIFY_SCOPES),
        show_dialog=True
    )
    auth_url = sp_oauth.get_authorize_url()
    return redirect(auth_url)

#Här hanterar vi vad vi gör med callbacken
@app.route('/spotify/callback')
@login_required
def spotify_callback():
    try:
        sp_oauth = SpotifyOAuth(
            client_id=SPOTIFY_CLIENT_ID,
            client_secret=SPOTIFY_CLIENT_SECRET,
            redirect_uri=SPOTIFY_REDIRECT_URI,
            scope=' '.join(SPOTIFY_SCOPES)
        )
        
        # Vi kollar om vi har fått en token
        code = request.args.get('code')
        if not code:
            flash('No authorization code found.', 'error')
            return redirect(url_for('profile', username=current_user.username))

        # Vi tar fram datan från token:en och har lite felhantering
        token_info = sp_oauth.get_access_token(code, as_dict=True)
        
        if not token_info:
            flash('Failed to retrieve access token.', 'error')
            return redirect(url_for('profile', username=current_user.username))

        # Vi skapar en Spotify client med token:en
        sp = spotipy.Spotify(auth=token_info['access_token'])
        
        # Hämtar användarens playlists.
        spotify_user = sp.current_user()
        
        # Updaterar användarens Spotify information
        current_user.spotify_user_id = spotify_user['id']
        current_user.spotify_access_token = token_info['access_token']
        current_user.spotify_refresh_token = token_info.get('refresh_token')
        current_user.spotify_token_expiry = datetime.utcnow() + timedelta(seconds=token_info['expires_in'])
        
        db.session.commit()
        
        flash('Successfully connected to Spotify!', 'success')
        return redirect(url_for('profile', username=current_user.username))
    
    #Felhantering
    except Exception as e:
        # Log the full error for debugging
        print(f"Spotify Callback Error: {e}")
        import traceback
        print(traceback.format_exc())
        
        flash(f'An error occurred: {str(e)}', 'error')
        return redirect(url_for('profile', username=current_user.username))

#Disconnectar Spotify
@app.route('/spotify/disconnect')
@login_required
def spotify_disconnect():
    #Rensar all data så att användaren blir utloggad
    current_user.spotify_access_token = None
    current_user.spotify_refresh_token = None
    current_user.spotify_user_id = None
    current_user.spotify_token_expiry = None
    
    db.session.commit()
    
    flash('Spotify account disconnected.')
    return redirect(url_for('profile', username=current_user.username))

@app.context_processor
def spotify_context_processor():
    
    # Context_processorn hjälper att lägga till Spotify relaterade funktioner tillgänliga överallt
    
    def get_spotify_top_tracks(user):
        
        #Kollar om användaren inte är inloggad, returnerar tom lista isåfall
        if not user.spotify_access_token:
            return []
        
        #Försöker skapa en Spotify client och hämta deras 5 top tracks
        try:
            # Refresh token if needed
            if (user.spotify_token_expiry and 
                datetime.utcnow() >= user.spotify_token_expiry):
                refresh_spotify_token(user)
            
            sp = spotipy.Spotify(auth=user.spotify_access_token)
            top_tracks = sp.current_user_top_tracks(limit=5, time_range='medium_term')
            
            #Lägger låtarna i en lista
            formatted_tracks = []
            for track in top_tracks['items']:
                # Generate embed URL
                embed_link = f"https://open.spotify.com/embed/track/{track['id']}"
                
                formatted_tracks.append({
                    'name': track['name'],
                    'artist': track['artists'][0]['name'],
                    'album_art': track['album']['images'][0]['url'] if track['album']['images'] else None,
                    'external_url': track['external_urls']['spotify'],
                    'preview_url': track['preview_url'],
                    'spotify_id': track['id'],
                    'embed_url': embed_link
                })
            
            return formatted_tracks
        except Exception as e:
            print(f"Error fetching top tracks: {e}")
            return []

    def get_spotify_playlists(user):
        
        #Kollar likadant här om användaren är inloggad och har en token
        if not user.spotify_access_token:
            return []
        
        #Försöker göra en Spotify Client och hämta max 6 spellistor
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
        
    #Kallar och returnerar resultatet av dessa funktioner
    return dict(
        get_spotify_top_tracks=get_spotify_top_tracks,
        get_spotify_playlists=get_spotify_playlists
    )

# FÖR ATT KÖRA PROGRAMMET
if __name__ == '__main__':
    with app.app_context():
        db.create_all()
    app.run(debug=True)
