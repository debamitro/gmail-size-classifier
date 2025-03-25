import os
import logging
import json
from flask import Flask, render_template, redirect, url_for, session, request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
from google.auth.transport.requests import Request
from datetime import datetime
from urllib.parse import urlencode

# Set up logging
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

app = Flask(__name__)
# Use a fixed secret key for development
app.secret_key = 'dev-secret-key'  # Change this in production

# OAuth 2.0 configuration
CLIENT_SECRETS_FILE = "credentials.json"
SCOPES = ['https://www.googleapis.com/auth/gmail.readonly']

def get_gmail_service():
    try:
        creds = None
        if 'credentials' in session:
            logger.debug("Found credentials in session")
            creds = Credentials(**session['credentials'])
        
        if not creds or not creds.valid:
            logger.debug("Credentials not valid, starting OAuth flow")
            if creds and creds.expired and creds.refresh_token:
                logger.debug("Refreshing expired credentials")
                creds.refresh(Request())
            else:
                return None
            
            session['credentials'] = {
                'token': creds.token,
                'refresh_token': creds.refresh_token,
                'token_uri': creds.token_uri,
                'client_id': creds.client_id,
                'client_secret': creds.client_secret,
                'scopes': creds.scopes
            }
        
        return build('gmail', 'v1', credentials=creds)
    except Exception as e:
        logger.error(f"Error in get_gmail_service: {str(e)}", exc_info=True)
        return None

@app.route('/')
def index():
    try:
        logger.debug("Session contents: %s", {k: '...' for k in session.keys()})
        
        if 'credentials' not in session:
            logger.debug("No credentials in session, showing login page")
            return render_template('index.html', authenticated=False)
        
        service = get_gmail_service()
        if not service:
            logger.debug("No valid service, redirecting to login")
            return render_template('index.html', authenticated=False)
        
        # Get max_results from query parameter, default to 1000
        max_results = request.args.get('max_results', default=1000, type=int)
        # Ensure max_results is between 1 and 5000
        max_results = min(max(max_results, 1), 5000)
        
        results = service.users().messages().list(userId='me', maxResults=max_results).execute()
        messages = results.get('messages', [])
        
        classified_emails = {'Small': [], 'Medium': [], 'Large': []}
        category_stats = {'Small': {'count': 0, 'total_size': 0}, 
                          'Medium': {'count': 0, 'total_size': 0}, 
                          'Large': {'count': 0, 'total_size': 0}}
        
        for message in messages:
            msg = service.users().messages().get(userId='me', id=message['id']).execute()
            size = msg['sizeEstimate']
            subject = ''
            for header in msg['payload']['headers']:
                if header['name'] == 'Subject':
                    subject = header['value']
                    break
            
            category = classify_email_size(size)
            classified_emails[category].append({
                'subject': subject,
                'size': size,
                'size_formatted': f"{size / 1024:.1f}KB" if size < 1024 * 1024 else f"{size / (1024 * 1024):.1f}MB",
                'thread_id': msg['threadId']
            })
            
            # Update category stats
            category_stats[category]['count'] += 1
            category_stats[category]['total_size'] += size
        
        # Sort emails within each category by size in descending order
        for category in classified_emails:
            classified_emails[category] = sorted(
                classified_emails[category], 
                key=lambda x: x['size'], 
                reverse=True
            )
            
            # Format total size for display
            if category_stats[category]['total_size'] < 1024 * 1024:
                category_stats[category]['total_size_formatted'] = f"{category_stats[category]['total_size'] / 1024:.1f} KB"
            else:
                category_stats[category]['total_size_formatted'] = f"{category_stats[category]['total_size'] / (1024 * 1024):.1f} MB"
        
        return render_template('index.html', 
                             authenticated=True, 
                             classified_emails=classified_emails,
                             category_stats=category_stats)
    
    except Exception as e:
        logger.error(f"Error in index route: {str(e)}", exc_info=True)
        return render_template('index.html', authenticated=False, error=str(e))

@app.route('/login')
def login():
    try:
        flow = Flow.from_client_secrets_file(CLIENT_SECRETS_FILE, scopes=SCOPES)
        flow.redirect_uri = 'http://127.0.0.1:5000/oauth2callback'
        
        authorization_url, state = flow.authorization_url(
            access_type='offline',
            include_granted_scopes='true'
        )
        
        session['state'] = state
        logger.debug(f"Starting OAuth flow, state: {state}")
        return redirect(authorization_url)
    except Exception as e:
        logger.error(f"Error in login: {str(e)}", exc_info=True)
        return render_template('index.html', authenticated=False, error=f"Login error: {str(e)}")

@app.route('/oauth2callback')
def oauth2callback():
    try:
        logger.debug("Received OAuth callback")
        logger.debug(f"Request URL: {request.url}")
        logger.debug(f"Session state: {session.get('state')}")
        
        state = session.get('state')
        if not state:
            raise ValueError("No state in session")
        
        flow = Flow.from_client_secrets_file(CLIENT_SECRETS_FILE, scopes=SCOPES, state=state)
        flow.redirect_uri = 'http://127.0.0.1:5000/oauth2callback'
        
        authorization_response = request.url
        flow.fetch_token(authorization_response=authorization_response)
        
        credentials = flow.credentials
        session['credentials'] = {
            'token': credentials.token,
            'refresh_token': credentials.refresh_token,
            'token_uri': credentials.token_uri,
            'client_id': credentials.client_id,
            'client_secret': credentials.client_secret,
            'scopes': credentials.scopes
        }
        
        logger.debug("Successfully stored credentials in session")
        return redirect(url_for('index'))
    except Exception as e:
        logger.error(f"Error in oauth2callback: {str(e)}", exc_info=True)
        return render_template('index.html', authenticated=False, error=f"Authentication error: {str(e)}")

def classify_email_size(size_bytes):
    if size_bytes < 100 * 1024:  # Less than 100KB
        return 'Small'
    elif size_bytes < 1024 * 1024:  # Less than 1MB
        return 'Medium'
    else:  # 1MB or larger
        return 'Large'

if __name__ == '__main__':
    os.environ['OAUTHLIB_INSECURE_TRANSPORT'] = '1'  # For development only
    app.run(host='127.0.0.1', port=5000, debug=True)
