from typing import Dict, Any, Optional
import boto3
from botocore.exceptions import ClientError
import os
from dotenv import load_dotenv
import requests
import jwt
import hmac
import hashlib
import base64
import json

load_dotenv()

cognito = boto3.client('cognito-idp',
    region_name=os.getenv('AWS_REGION'),
    aws_access_key_id=os.getenv('AWS_ACCESS_KEY_ID'),
    aws_secret_access_key=os.getenv('AWS_SECRET_ACCESS_KEY')
)

USER_POOL_ID = os.getenv('COGNITO_USER_POOL_ID')
CLIENT_ID = os.getenv('COGNITO_CLIENT_ID')
CLIENT_SECRET = os.getenv('COGNITO_CLIENT_SECRET')

def get_secret_hash(username: str) -> str:
    """Calculate the secret hash for Cognito operations"""
    message = username + CLIENT_ID
    key = os.getenv('COGNITO_CLIENT_SECRET').encode('utf-8')
    message = message.encode('utf-8')
    return base64.b64encode(
        hmac.new(key, message, digestmod=hashlib.sha256).digest()
    ).decode()

def format_phone_number(phone: str) -> str:
    """Format phone number to E.164 format (+1234567890)"""
    digits = ''.join(filter(str.isdigit, phone))
    if not phone.startswith('+'):
        if len(digits) == 10:  # Assuming Indian numbers
            return f"+91{digits}"
        else:
            return f"+{digits}"
    return phone

def decode_token(token: str) -> dict:
    """Decode a JWT token and return its payload"""
    try:
        parts = token.split('.')
        if len(parts) != 3:
            raise Exception("Invalid token format")
        payload = parts[1]
        payload += '=' * (-len(payload) % 4)
        decoded_payload = base64.b64decode(payload)
        return json.loads(decoded_payload)
    except Exception as e:
        print(f"Error decoding token: {str(e)}")
        return {}

def get_cognito_username(email: str, id_token: str = None) -> str:
    """Get the correct Cognito username based on the authentication method"""
    try:
        if id_token:
            token_payload = decode_token(id_token)
            # print(f"Token payload: {token_payload}")
            
            cognito_username = token_payload.get('cognito:username')
            if cognito_username:
                print(f"Found cognito:username in token: {cognito_username}")
                return cognito_username
        return email
    except Exception as e:
        print(f"Error getting Cognito username: {str(e)}")
        return email

def check_existing_user(email: str, phone_number: str = None):
    """Check if user exists by email or phone, including Google-authenticated users"""
    try:
        # First check by email
        try:
            # List users with email filter
            existing_users = cognito.list_users(
                UserPoolId=USER_POOL_ID,
                Filter=f'email = "{email}"'
            )
            
            if existing_users.get('Users', []):
                user = existing_users['Users'][0]
                # Check if phone number is verified
                phone_verified = False
                current_phone = None
                for attr in user['Attributes']:
                    if attr['Name'] == 'phone_number_verified' and attr['Value'] == 'true':
                        phone_verified = True
                    elif attr['Name'] == 'phone_number':
                        current_phone = attr['Value']
                
                return {
                    "exists": True,
                    "phone_verified": phone_verified,
                    "current_phone": current_phone,
                    "user": user
                }
                
        except ClientError as e:
            if 'Filter' not in str(e):
                raise e
        
        # Then check by phone if provided
        if phone_number:
            try:
                existing_by_phone = cognito.list_users(
                    UserPoolId=USER_POOL_ID,
                    Filter=f'phone_number = "{phone_number}"'
                )
                if existing_by_phone.get('Users', []):
                    return {
                        "exists": True,
                        "phone_exists": True,
                        "user": existing_by_phone['Users'][0]
                    }
            except ClientError as e:
                if 'Filter' not in str(e):
                    raise e
        
        return {"exists": False}
        
    except Exception as e:
        print(f"Error checking existing user: {str(e)}")
        raise e

def google_initiate_auth(username: str) -> dict:
    """Get authentication tokens for Google users using custom auth flow"""
    try:
        # Start custom auth flow
        auth_response = cognito.admin_initiate_auth(
            UserPoolId=USER_POOL_ID,
            ClientId=CLIENT_ID,
            AuthFlow='CUSTOM_AUTH',
            AuthParameters={
                'USERNAME': username,
                'SECRET_HASH': get_secret_hash(username)
            }
        )
        
        # Handle challenge if present
        if 'ChallengeName' in auth_response:
            challenge_response = cognito.admin_respond_to_auth_challenge(
                UserPoolId=USER_POOL_ID,
                ClientId=CLIENT_ID,
                ChallengeName=auth_response['ChallengeName'],
                ChallengeResponses={
                    'USERNAME': username,
                    'SECRET_HASH': get_secret_hash(username),
                    'ANSWER': 'GOOGLE_VERIFIED'
                },
                Session=auth_response.get('Session')
            )
            
            # Return the final authentication result
            if 'AuthenticationResult' in challenge_response:
                return challenge_response['AuthenticationResult']
                
        # If no challenge, return the initial auth result
        return auth_response.get('AuthenticationResult', {})
        
    except Exception as e:
        print(f"Error in google_initiate_auth: {str(e)}")
        return {}