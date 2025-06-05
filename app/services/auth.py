import boto3
from botocore.exceptions import ClientError
import os
from dotenv import load_dotenv
import requests
from typing import Optional
import jwt
import hmac
import hashlib
import base64
import json
import asyncio
from app.services import dynamodb as dynamodb_service
from app.utils.cognito_utils import (check_existing_user, get_cognito_username, format_phone_number, decode_token, 
get_secret_hash, USER_POOL_ID, CLIENT_ID, CLIENT_SECRET, google_initiate_auth)
from fastapi import HTTPException, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
import names
from datetime import datetime
load_dotenv()

cognito = boto3.client('cognito-idp',
    region_name=os.getenv('AWS_REGION')
    # aws_access_key_id=os.getenv('AWS_ACCESS_KEY_ID'),
    # aws_secret_access_key=os.getenv('AWS_SECRET_ACCESS_KEY')
)

USERS_TABLE = os.getenv('DYNAMODB_USER_TABLE')

security = HTTPBearer()

async def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(security)) -> dict:
    """
    Validate JWT token and return current user information
    Modified to handle API Gateway format
    """
    try:
        # Handle both direct Bearer token and API Gateway format
        token = credentials.credentials
        
        # Check if token is from API Gateway
        if token.startswith('{'):
            try:
                # Parse API Gateway authorization context
                auth_data = json.loads(token)
                token = auth_data.get('authorizationToken', '').replace('Bearer ', '')
            except json.JSONDecodeError:
                pass
                
        print(f"Processing token: {token[:20]}...")  # Debug log
        
        decoded_token = decode_token(token)
        print(f"Decoded token payload: {decoded_token}")  # Debug log
        
        if not decoded_token:
            raise HTTPException(status_code=401, detail="Invalid token")

        # Get username from token
        username = decoded_token.get('username')
        if not username:
            raise HTTPException(status_code=401, detail="Invalid token payload")

        try:
            # Use username directly to get user data
            user = cognito.admin_get_user(
                UserPoolId=USER_POOL_ID,
                Username=username
            )

            # Extract user attributes
            user_attrs = {}
            for attr in user['UserAttributes']:
                user_attrs[attr['Name']] = attr['Value']

            if not user_attrs.get('email'):
                raise HTTPException(status_code=401, detail="User email not found")

            return {
                "email": user_attrs.get('email'),
                "sub": user_attrs.get('sub'),
                "phone_number": user_attrs.get('phone_number'),
                "name": user_attrs.get('name')
            }

        except cognito.exceptions.UserNotFoundException:
            raise HTTPException(status_code=401, detail="User not found")

    except Exception as e:
        print(f"Authentication error: {str(e)}")  # Debug log
        raise HTTPException(status_code=401, detail=str(e))

async def user_sign_up(data):
    """Pre-signup verification flow"""
    try:
        formatted_phone = format_phone_number(data.mobile)
        print(data.email, formatted_phone)
        # Check for existing users (including Google-authenticated ones)
        existing_check = check_existing_user(data.email, formatted_phone)
        
        if existing_check["exists"]:
            if existing_check.get("phone_verified"):
                raise Exception("User with this email already exists")
            elif existing_check.get("phone_exists"):
                raise Exception("User with this phone number already exists")
            else:
                # User exists but phone not verified
                print(f"User exists but phone not verified: {data.email}")
                current_phone = existing_check.get("current_phone")
                
                # Update phone number if different
                if current_phone != formatted_phone:
                    cognito.admin_update_user_attributes(
                        UserPoolId=USER_POOL_ID,
                        Username=data.email,
                        UserAttributes=[
                            {'Name': 'phone_number', 'Value': formatted_phone}
                        ]
                    )
                
                return {
                    "status": "VERIFICATION_REQUIRED",
                    "message": "Please verify your phone number to complete signup.",
                    "email": data.email,
                    "phone_number": formatted_phone,
                    "show_verification": True
                }
                
    except cognito.exceptions.UserNotFoundException:
            pass  

    try:
        # existing_by_phone = cognito.list_users(
        #     UserPoolId=USER_POOL_ID,
        #     Filter=f'phone_number = "{formatted_phone}"'
        # )
        # if existing_by_phone.get('Users', []):
        #     raise Exception("User with this phone number already exists")
        
        print(f"Creating new user with email: {data.email}")  # This will now print for new users
        
        # Create temporary user attributes for verification
        response = cognito.sign_up(
            ClientId=CLIENT_ID,
            Username=data.email,  # Use email as username
            Password=data.password,
            UserAttributes=[
                {'Name': 'email', 'Value': data.email},
                {'Name': 'phone_number', 'Value': formatted_phone}
            ],
            SecretHash=get_secret_hash(data.email)
        )
        
        print(f"Signup response: {response}")  # Debug log
        
        return {
            "status": "VERIFICATION_REQUIRED",
            "message": "Please verify your phone number to complete signup.",
            "email": data.email,
            "phone_number": formatted_phone,
            "show_verification": True,
            "user_sub": response.get('UserSub')
        }
    except ClientError as e:
        print(f"Signup error: {str(e)}")  # Debug log
        raise Exception(e.response['Error']['Message'])

async def verify_sms_code(phone_number: str, verification_code: str, email: Optional[str] = None):
    """Verify phone number and complete user signup"""
    try:
        formatted_phone = format_phone_number(phone_number)
        cognito_username = None
        
        # Try to find cognito_username if not provided
        if email:
            try:
                existing_users = cognito.list_users(
                    UserPoolId=USER_POOL_ID,
                    Filter=f'email = "{email}"'
                )
                
                for user in existing_users.get('Users', []):
                    is_google_user = any(
                        id.get('providerName') == 'Google'
                        for attr in user['Attributes'] if attr['Name'] == 'identities'
                        for id in json.loads(attr['Value'])
                    )
                    if is_google_user:
                        cognito_username = user['Username']
                        print(f"Found Google user cognito_username: {cognito_username}")
                        break
            except Exception as e:
                print(f"Error finding cognito username: {str(e)}")
        
        # For Google users with cognito_username
        if cognito_username:
            print(f"Using Google cognito username for verification: {cognito_username}")
            
            try:
                # Get fresh tokens
                auth_result = google_initiate_auth(cognito_username)
                if not auth_result:
                    raise Exception("Failed to get authentication tokens")
                
                # Verify the phone number attribute using the access token
                cognito.verify_user_attribute(
                    AccessToken=auth_result['AccessToken'],
                    AttributeName='phone_number',
                    Code=verification_code
                )

                cognito.admin_update_user_attributes(
                    UserPoolId=USER_POOL_ID,
                    Username=cognito_username,
                    UserAttributes=[
                        {'Name': 'phone_number_verified', 'Value': 'true'},
                        {'Name': 'email_verified', 'Value': 'true'}
                    ]
                )

                return {
                    "status": "SUCCESS",
                    "message": "Phone number verified successfully. You can now log in.",
                    "tokens": auth_result
                }
            except Exception as e:
                print(f"Error verifying phone number: {str(e)}")
                raise Exception("Failed to verify phone number")
                
        else:
            # For regular signup, use email as username if provided
            username = email if email else formatted_phone
            print("Processing regular user verification")
            
            response = cognito.confirm_sign_up(
                ClientId=CLIENT_ID,
                Username=username,
                ConfirmationCode=verification_code,
                SecretHash=get_secret_hash(username)
            )
            print(f"Confirm signup response: {response}")
            
            # Update user attributes after verification
            cognito.admin_update_user_attributes(
                UserPoolId=USER_POOL_ID,
                Username=username,
                UserAttributes=[
                    {'Name': 'phone_number_verified', 'Value': 'true'},
                    {'Name': 'email_verified', 'Value': 'true'}
                ]
            )
            print("Regular user attributes updated successfully")
            
            return {
                "status": "SUCCESS",
                "message": "Phone number verified successfully. You can now log in."
            }
            
    except Exception as e:
        print(f"Verification error: {str(e)}")
        raise Exception(str(e))

async def resend_verification_code(phone_number: str, email: Optional[str] = None):
    """Resend verification code"""
    try:
        formatted_phone = format_phone_number(phone_number)
        cognito_username = None
        # Try to find cognito_username if not provided
        if email:
            try:
                existing_users = cognito.list_users(
                    UserPoolId=USER_POOL_ID,
                    Filter=f'email = "{email}"'
                )
                
                for user in existing_users.get('Users', []):
                    is_google_user = any(
                        id.get('providerName') == 'Google'
                        for attr in user['Attributes'] if attr['Name'] == 'identities'
                        for id in json.loads(attr['Value'])
                    )
                    if is_google_user:
                        cognito_username = user['Username']
                        print(f"Found Google user cognito_username: {cognito_username}")
                        break
            except Exception as e:
                print(f"Error finding cognito username: {str(e)}")
        
        # For Google users with cognito_username
        if cognito_username:
            print(f"Using Google cognito username for verification: {cognito_username}")
            
            try:
                # Get fresh tokens
                auth_result = google_initiate_auth(cognito_username)
                if not auth_result:
                    raise Exception("Failed to get authentication tokens")
                
                # First ensure phone number is set
                cognito.admin_update_user_attributes(
                    UserPoolId=USER_POOL_ID,
                    Username=cognito_username,
                    UserAttributes=[
                        {'Name': 'phone_number', 'Value': formatted_phone},
                        {'Name': 'phone_number_verified', 'Value': 'false'}
                    ]
                )
                
                # Request verification code using new access token
                cognito.get_user_attribute_verification_code(
                    AccessToken=auth_result['AccessToken'],
                    AttributeName='phone_number'
                )
                
                return {
                    "status": "SUCCESS",
                    "message": "Verification code sent successfully",
                    "tokens": auth_result
                }
            except Exception as e:
                print(f"Error sending verification code: {str(e)}")
                raise Exception("Failed to send verification code")
        
        else:
            # For regular sign-up users
            username = email if email else formatted_phone
            print("Processing regular user verification")
            
            response = cognito.resend_confirmation_code(
                ClientId=CLIENT_ID,
                Username=username,
                SecretHash=get_secret_hash(username)
            )
            print(f"Resend code response: {response}")
            
            return {
                "status": "SUCCESS",
                "message": "Verification code resent successfully"
            }
                
    except cognito.exceptions.UserNotFoundException:
        raise Exception("User not found. Please sign up first.")
    except cognito.exceptions.LimitExceededException:
        raise Exception("Too many attempts. Please try again later.")
    except Exception as e:
        print(f"Resend code error: {str(e)}")
        raise Exception(str(e))

async def get_google_auth_url(email: Optional[str] = None):
    """Get Google OAuth URL using Cognito's hosted UI, with optional pre-check for existing users"""
    try:
        # If email is provided, check for existing non-Google user first
        if email:
            try:
                print(f"Checking for existing user with email: {email}")  # Debug log
                existing_users = cognito.list_users(
                    UserPoolId=USER_POOL_ID,
                    Filter=f'email = "{email}"'
                )
                
                if existing_users.get('Users', []):
                    existing_user = existing_users['Users'][0]
                    print(f"Found existing user attributes: {existing_user['Attributes']}")  # Debug log
                    
                    # Check if user was created through Google
                    is_google_user = False
                    for attr in existing_user['Attributes']:
                        if attr['Name'] == 'identities':
                            identities = json.loads(attr['Value'])
                            print(f"Found identities: {identities}")  # Debug log
                            is_google_user = any(id.get('providerName') == 'Google' for id in identities)
                            break
                    
                    print(f"Is Google user: {is_google_user}")  # Debug log
                    
                    # If user exists and is not a Google user, block the signup
                    if not is_google_user:
                        print("Blocking signup - non-Google user exists")  # Debug log
                        raise Exception("An account with this email already exists. Please sign in with your email and password.")
            except Exception as e:
                print(f"Error checking existing user: {str(e)}")
                # Re-raise the exception to stop the flow
                raise e
        
        # Generate Google auth URL
        domain_prefix = os.getenv('COGNITO_DOMAIN_PREFIX')
        region = os.getenv('AWS_REGION', 'ap-south-1')
        domain = f"{domain_prefix}.auth.{region}.amazoncognito.com"
        redirect_uri = os.getenv('GOOGLE_REDIRECT_URI', 'https://main.d144grx5jxki07.amplifyapp.com/api/auth/callback')
        
        # Ensure URL-encoding for redirect_uri
        encoded_redirect = requests.utils.quote(redirect_uri, safe='')
        
        auth_url = f"https://{domain}/oauth2/authorize?" + \
                  f"response_type=code&" + \
                  f"client_id={CLIENT_ID}&" + \
                  f"redirect_uri={encoded_redirect}&" + \
                  f"identity_provider=Google&" + \
                  f"scope=openid+email+profile"
                  
        print(f"Generated auth URL: {auth_url}")  # Debug logging
        return {
            "status": "SUCCESS",
            "url": auth_url
        }
    except Exception as e:
        print(f"Error generating Google auth URL: {str(e)}")
        raise Exception(f"Failed to generate Google auth URL: {str(e)}")

async def handle_google_callback(code: str):
    """Handle Google OAuth callback and phone verification if needed"""
    try:
        domain_prefix = os.getenv('COGNITO_DOMAIN_PREFIX')
        region = os.getenv('AWS_REGION', 'ap-south-1')
        redirect_uri = os.getenv('GOOGLE_REDIRECT_URI', 'http://localhost:3000/api/auth/callback')
        
        # Exchange code for tokens
        token_endpoint = f"https://{domain_prefix}.auth.{region}.amazoncognito.com/oauth2/token"
        token_response = requests.post(
            token_endpoint,
            data={
                'grant_type': 'authorization_code',
                'client_id': CLIENT_ID,
                'client_secret': os.getenv('COGNITO_CLIENT_SECRET'),
                'code': code,
                'redirect_uri': redirect_uri
            },
            headers={'Content-Type': 'application/x-www-form-urlencoded'}
        )
        
        if token_response.status_code != 200:
            # print(f"Token exchange failed: {token_response.text}")
            raise Exception("Failed to exchange code for tokens")
            
        tokens = token_response.json()
        id_token = tokens['id_token']
        token_payload = decode_token(id_token)
        # print("=== DEBUG INFO ===")
        # print(f"Token payload: {json.dumps(token_payload, indent=2)}")
        
        email = token_payload.get('email')
        if not email:
            raise Exception("Email not found in token claims")

        # Check for existing non-Google user BEFORE any user creation
        try:
            existing_users = cognito.list_users(
                UserPoolId=USER_POOL_ID,
                Filter=f'email = "{email}"'
            )
            
            manual_user_exists = False
            google_account_username = None

            for user in existing_users.get('Users', []):
                is_google_user = any(
                    id.get('providerName') == 'Google'
                    for attr in user['Attributes'] if attr['Name'] == 'identities'
                    for id in json.loads(attr['Value'])
                )
                if is_google_user:
                    google_account_username = user['Username']
                else:
                    manual_user_exists = True
                
                # If user exists and is not a Google user, block the signup
                
                if manual_user_exists and google_account_username:
                    try:
                        cognito.admin_delete_user(
                            UserPoolId=USER_POOL_ID,
                            Username=google_account_username
                        )
                        print(f"Deleted Google account for email: {email}")
                    except cognito.exceptions.UserNotFoundException:
                        print(f"No Google user to delete for email: {email}")

                    raise Exception(
                        "An account with this email already exists. Please sign in with your email and password."
                    )
                    
        except cognito.exceptions.UserNotFoundException:
            print(f"New Google user detected: {cognito_username}")  # Debug log
            # Create new user with Google auth type
            cognito.admin_create_user(
                UserPoolId=USER_POOL_ID,
                Username=cognito_username,
                UserAttributes=[
                    {'Name': 'email', 'Value': email},
                    {'Name': 'email_verified', 'Value': 'true'},
                ],
                MessageAction='SUPPRESS'
            )
            
            return {
                'status': 'PHONE_NUMBER_REQUIRED',
                'email': email,
                'cognito_username': cognito_username,
                'name': token_payload.get('name', ''),
                'picture': token_payload.get('picture', ''),
                'google_tokens': tokens,
                'phone_verified': False
            }

        cognito_username = get_cognito_username(email, id_token)

        # Now check DynamoDB for registration status
        try:
            user_data = dynamodb_service.get_item(
                table_name=USERS_TABLE,
                key={'email': {'S': email}}
            )
            print("user data:", user_data)  # Debug log
            
            # Check if user exists in DynamoDB and has data
            if user_data:
                is_registered = user_data.get('isRegistered', {}).get('BOOL', False)
                existing_phone = user_data.get('mobile', {}).get('S')
                
                print(f"DynamoDB user data - registered: {is_registered}, phone: {existing_phone}")
                
                # Only proceed if both registered and has phone
                if is_registered and existing_phone:
                    try:
                        # User exists and is registered, set phone as verified
                        cognito.admin_update_user_attributes(
                            UserPoolId=USER_POOL_ID,
                            Username=cognito_username,
                            UserAttributes=[
                                {'Name': 'phone_number', 'Value': existing_phone},
                                {'Name': 'phone_number_verified', 'Value': 'true'}
                            ]
                        )
                        
                        return {
                            'status': 'SUCCESS',
                            'tokens': tokens,
                            'user': {
                                'email': email,
                                'name': token_payload.get('name', ''),
                                'picture': token_payload.get('picture', ''),
                                'phone_number': existing_phone,
                                'phone_verified': True
                            }
                        }
                    except Exception as e:
                        print(f"Error updating Cognito attributes: {str(e)}")
                        # If Cognito update fails, still require phone verification
                        return {
                            'status': 'PHONE_NUMBER_REQUIRED',
                            'email': email,
                            'cognito_username': cognito_username,
                            'name': token_payload.get('name', ''),
                            'picture': token_payload.get('picture', ''),
                            'google_tokens': tokens,
                            'phone_verified': False
                        }
            
            # If we reach here, either user doesn't exist or isn't fully registered
            print(f"User not found or not registered in DynamoDB: {email}")
            return {
                'status': 'PHONE_NUMBER_REQUIRED',
                'email': email,
                'cognito_username': cognito_username,
                'name': token_payload.get('name', ''),
                'picture': token_payload.get('picture', ''),
                'google_tokens': tokens,
                'phone_verified': False
            }
                
        except Exception as e:
            print(f"User not found in DynamoDB: {str(e)}")
            return {
                'status': 'PHONE_NUMBER_REQUIRED',
                'email': email,
                'cognito_username': cognito_username,
                'name': token_payload.get('name', ''),
                'picture': token_payload.get('picture', ''),
                'google_tokens': tokens,
                'phone_verified': False
            }
            
    except Exception as e:
        print(f"Google callback error: {str(e)}")
        raise Exception(f"Google authentication failed: {str(e)}")
            
async def complete_google_signup(email: str, phone_number: str, google_tokens: dict):
    """Add and verify phone number for Google-authenticated user"""
    try:
        formatted_phone = format_phone_number(phone_number)
        token_payload = decode_token(google_tokens['id_token'])
        cognito_username = get_cognito_username(email, google_tokens['id_token'])
        print(f"Completing signup for Cognito username: {cognito_username}")
            
        # Check for existing phone number
        try:
            existing_by_phone = cognito.list_users(
                UserPoolId=USER_POOL_ID,
                Filter=f'phone_number = "{formatted_phone}"'
            )
            
            if existing_by_phone.get('Users', []):
                for existing_user in existing_by_phone['Users']:
                    if existing_user['Username'] != cognito_username:
                        print(f"Phone number already exists for different user: {existing_user['Username']}")
                        raise Exception("A user with this phone number already exists")
                print("Phone number belongs to same user, proceeding with update")
        except cognito.exceptions.UserNotFoundException:
            pass
        
        fname = token_payload.get('name', '')
        gender = token_payload.get('gender', 'Not Specified')
        uname = names.get_full_name(gender=gender.lower() if gender != 'Not Specified' else None)

        user_item = {
            'username': {'S': uname},
            'fullname': {'S': fname},
            'mobile': {'S': formatted_phone},
            'email': {'S': email},
            'gender': {'S': gender.lower()},
            'created_at': {'S': str(datetime.now())},
            'updated_at': {'S': str(datetime.now())},
            'isVerified': {'S': "UNVERIFIED"},
            'isDesigner': {'BOOL': False},
            'isRegistered': {'BOOL': False},
            'collections': {'NULL': True},
            'cart': {'NULL': True},
            'sign_in_method': {'S': 'google'}
        }
        try:
            dynamodb_service.put_item(table_name=USERS_TABLE, item=user_item)
           
            # Update phone number
            cognito.admin_update_user_attributes(
                UserPoolId=USER_POOL_ID,
                Username=cognito_username,
                UserAttributes=[
                    {'Name': 'phone_number', 'Value': formatted_phone},
                    {'Name': 'phone_number_verified', 'Value': 'false'}
                ]
            )
            
            # Get new tokens using admin_initiate_auth
            auth_result = google_initiate_auth(cognito_username)
            if not auth_result:
                raise Exception("Failed to get authentication tokens")
                
            print("Got new auth tokens for verification")

            
            # Request verification code using new access token
            cognito.get_user_attribute_verification_code(
                AccessToken=auth_result['AccessToken'],
                AttributeName='phone_number'
                )
            
            return {
                'status': 'VERIFICATION_REQUIRED',
                'message': 'Please verify your phone number',
                'email': email,
                'cognito_username': cognito_username,
                'phone_number': formatted_phone,
                'google_tokens': {
                    'AccessToken': auth_result['AccessToken'],
                    'IdToken': auth_result['IdToken'],
                    'RefreshToken': auth_result.get('RefreshToken')
                }
            }
            
        except cognito.exceptions.UserNotFoundException:
            raise Exception("User not found. Please sign in with Google first.")

        
    except Exception as e:
        print(f"Complete Google signup error: {str(e)}")
        raise Exception(f"Failed to complete Google signup: {str(e)}")

async def user_sign_in(data):
    """Sign in user with email and password"""
    try:
        # Initiate auth and get tokens
        auth_response = cognito.initiate_auth(
            # UserPoolId=USER_POOL_ID,
            ClientId=CLIENT_ID,
            AuthFlow='USER_PASSWORD_AUTH',
            AuthParameters={
                'USERNAME': data.email,
                'PASSWORD': data.password,
                'SECRET_HASH': get_secret_hash(data.email)
            }
        )
        
        # Get user attributes
        user = cognito.admin_get_user(
            UserPoolId=USER_POOL_ID,
            Username=data.email
        )
        
        # Extract user data
        user_data = {}
        for attr in user['UserAttributes']:
            user_data[attr['Name']] = attr['Value']
        
        return {
            "status": "SUCCESS",
            "message": "Successfully signed in",
            "tokens": auth_response['AuthenticationResult'],
            "user": {
                "email": user_data.get('email'),
                "phone_number": user_data.get('phone_number'),
                "name": user_data.get('name', ''),
                "sub": user_data.get('sub')
            }
        }
        
    except cognito.exceptions.NotAuthorizedException:
        raise Exception("Invalid email or password")
    except cognito.exceptions.UserNotFoundException:
        raise Exception("User not found")
    except cognito.exceptions.UserNotConfirmedException:
        raise Exception("Please verify your account first")
    except Exception as e:
        print(f"Sign in error: {str(e)}")
        raise Exception(f"Failed to sign in: {str(e)}")

async def user_sign_out(access_token: str):
    """Sign out user"""
    try:
        cognito.global_sign_out(
            AccessToken=access_token
        )
        return {"status": "SUCCESS", "message": "Successfully signed out"}           
    except Exception as e:
        print(f"Sign out error: {str(e)}")
        raise Exception(f"Failed to sign out: {str(e)}")

async def verify_designer_status(
    current_user: dict,
    dynamodb = Depends(dynamodb_service._get_dynamodb_client)
) -> bool:
    try:
        response = dynamodb.get_item(
            TableName=USERS_TABLE,
            Key={
                'email': {'S': current_user['email']}
            }
        )
        
        if 'Item' not in response:
            return False
            
        return response['Item'].get('isDesigner', {}).get('BOOL', False)
    except Exception as e:
        print(f"Error checking designer status: {str(e)}")
        return False

async def initiate_forgot_password(email: str):
    """Initiate the forgot password flow"""
    try:
        # Check if user exists
        existing_check = check_existing_user(email)
        if not existing_check["exists"]:
            raise Exception("No account found with this email address")

        # Check if it's a Google-authenticated user
        user = existing_check["user"]
        for attr in user['Attributes']:
            if attr['Name'] == 'identities':
                identities = json.loads(attr['Value'])
                if any(id.get('providerName') == 'Google' for id in identities):
                    raise Exception("This account uses Google Sign-In.")

        # Initiate forgot password flow
        response = cognito.forgot_password(
            ClientId=CLIENT_ID,
            Username=email,
            SecretHash=get_secret_hash(email)
        )

        return {
            "status": "SUCCESS",
            "message": "Password reset code sent to your email",
            "delivery": {
                "destination": response.get('CodeDeliveryDetails', {}).get('Destination', ''),
                "medium": response.get('CodeDeliveryDetails', {}).get('DeliveryMedium', 'EMAIL')
            }
        }

    except cognito.exceptions.UserNotFoundException:
        raise Exception("No account found with this email address")
    except cognito.exceptions.LimitExceededException:
        raise Exception("Too many attempts. Please try again later")
    except cognito.exceptions.InvalidParameterException as e:
        raise Exception(str(e))
    except Exception as e:
        print(f"Forgot password error: {str(e)}")
        raise Exception(str(e))

async def confirm_forgot_password(email: str, code: str, new_password: str):
    """Confirm forgot password with verification code"""
    try:
        # Validate password requirements
        if len(new_password) < 8:
            raise Exception("Password must be at least 8 characters long")
        
        # Check for uppercase, lowercase, number, and special character
        if not any(c.isupper() for c in new_password):
            raise Exception("Password must contain at least one uppercase letter")
        if not any(c.islower() for c in new_password):
            raise Exception("Password must contain at least one lowercase letter")
        if not any(c.isdigit() for c in new_password):
            raise Exception("Password must contain at least one number")
        if not any(not c.isalnum() for c in new_password):
            raise Exception("Password must contain at least one special character")

        # Confirm forgot password
        response = cognito.confirm_forgot_password(
            ClientId=CLIENT_ID,
            Username=email,
            ConfirmationCode=code,
            Password=new_password,
            SecretHash=get_secret_hash(email)
        )

        return {
            "status": "SUCCESS",
            "message": "Password has been reset successfully"
        }

    except cognito.exceptions.CodeMismatchException:
        raise Exception("Invalid verification code")
    except cognito.exceptions.ExpiredCodeException:
        raise Exception("Verification code has expired")
    except cognito.exceptions.UserNotFoundException:
        raise Exception("No account found with this email address")
    except cognito.exceptions.LimitExceededException:
        raise Exception("Too many attempts. Please try again later")
    except cognito.exceptions.InvalidPasswordException as e:
        raise Exception(str(e))
    except Exception as e:
        print(f"Reset password error: {str(e)}")
        raise Exception(str(e))

async def change_user_password(email: str, current_password: str, new_password: str):
    """Change user password"""
    try:
        # Validate password requirements
        if len(new_password) < 8:
            raise Exception("Password must be at least 8 characters long")
        
        # Check for uppercase, lowercase, number, and special character
        if not any(c.isupper() for c in new_password):
            raise Exception("Password must contain at least one uppercase letter")
        if not any(c.islower() for c in new_password):
            raise Exception("Password must contain at least one lowercase letter")
        if not any(c.isdigit() for c in new_password):
            raise Exception("Password must contain at least one number")
        if not any(not c.isalnum() for c in new_password):
            raise Exception("Password must contain at least one special character")

        # First verify the user exists and is not a Google user
        existing_check = check_existing_user(email)
        if not existing_check["exists"]:
            raise Exception("User not found")

        # Check if it's a Google-authenticated user
        user = existing_check["user"]
        for attr in user['Attributes']:
            if attr['Name'] == 'identities':
                identities = json.loads(attr['Value'])
                if any(id.get('providerName') == 'Google' for id in identities):
                    raise Exception("Password cannot be changed for Google-authenticated accounts")

        # Authenticate with current password first
        try:
            cognito.initiate_auth(
                ClientId=CLIENT_ID,
                AuthFlow='USER_PASSWORD_AUTH',
                AuthParameters={
                    'USERNAME': email,
                    'PASSWORD': current_password,
                    'SECRET_HASH': get_secret_hash(email)
                }
            )
        except cognito.exceptions.NotAuthorizedException:
            raise Exception("Current password is incorrect")

        # Change password
        cognito.admin_set_user_password(
            UserPoolId=USER_POOL_ID,
            Username=email,
            Password=new_password,
            Permanent=True
        )

        return {
            "status": "SUCCESS",
            "message": "Password updated successfully"
        }

    except Exception as e:
        print(f"Change password error: {str(e)}")
        raise Exception(str(e))