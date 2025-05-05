from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin

db = SQLAlchemy()

# Tabell för användaren
class User(db.Model, UserMixin):
    __tablename__ = 'users'
    userId = db.Column(db.String, primary_key=True)
    username = db.Column(db.String(80), nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password = db.Column(db.String(128))
    profilePicture = db.Column(db.String, nullable=True, default='default.jpg')
    favoriteGenres = db.Column(db.PickleType, nullable=True)
    createdAt = db.Column(db.DateTime, default=db.func.current_timestamp())
    spotify_access_token = db.Column(db.String(255), nullable=True)
    spotify_refresh_token = db.Column(db.String(255), nullable=True)
    spotify_user_id = db.Column(db.String(255), nullable=True)
    spotify_token_expiry = db.Column(db.DateTime, nullable=True)
    
    # För inloggning
    def get_id(self):
        return self.userId
    
    def is_authenticated(self):
        return True
    
    def is_active(self):
        return True
    
    def is_anonymous(self):
        return False

# Tabell för följare
class Follower(db.Model):
    __tablename__ = 'followers'
    followerId = db.Column(db.String, db.ForeignKey('users.userId'), primary_key=True)
    followingId = db.Column(db.String, db.ForeignKey('users.userId'), primary_key=True)

# Tabell för posts
class Post(db.Model):
    __tablename__ = 'posts'
    postId = db.Column(db.String, primary_key=True)
    userId = db.Column(db.String, db.ForeignKey('users.userId'), nullable=False)
    songId = db.Column(db.String, nullable=False)
    caption = db.Column(db.Text, nullable=True)
    likes = db.Column(db.Integer, default=0)
    createdAt = db.Column(db.DateTime, default=db.func.current_timestamp())

# Tabell för gilla-markeringar
class Like(db.Model):
    __tablename__ = 'likes'
    likeId = db.Column(db.String, primary_key=True)
    userId = db.Column(db.String, db.ForeignKey('users.userId'), nullable=False)
    postId = db.Column(db.String, db.ForeignKey('posts.postId'), nullable=False)

# Tabell för kommentarer
class Comment(db.Model):
    __tablename__ = 'comments'
    commentId = db.Column(db.String, primary_key=True)
    userId = db.Column(db.String, db.ForeignKey('users.userId'), nullable=False)
    postId = db.Column(db.String, db.ForeignKey('posts.postId'), nullable=False)
    content = db.Column(db.Text, nullable=False)
    createdAt = db.Column(db.DateTime, default=db.func.current_timestamp())

# Tabell för sånger
class Song(db.Model):
    __tablename__ = 'songs'
    songId = db.Column(db.String, primary_key=True)
    title = db.Column(db.String, nullable=False)
    artist = db.Column(db.String, nullable=False)
    album = db.Column(db.String, nullable=True)
    coverUrl = db.Column(db.String, nullable=True)