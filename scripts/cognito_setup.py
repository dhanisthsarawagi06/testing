import boto3
import json
import os
from dotenv import load_dotenv

load_dotenv()

def update_env_file(config):
    """Update .env file with new Cognito configuration"""
    env_path = os.path.join(os.path.dirname(__file__), '../.env')
    
    # Read existing .env content
    if os.path.exists(env_path):
        with open(env_path, 'r') as f:
            lines = f.readlines()
    else:
        lines = []
    
    # Update or add new values
    env_vars = {
        'COGNITO_USER_POOL_ID': config['USER_POOL_ID'],
        'COGNITO_CLIENT_ID': config['CLIENT_ID'],
        'COGNITO_CLIENT_SECRET': config['CLIENT_SECRET'],
        'COGNITO_DOMAIN_PREFIX': config['DOMAIN_PREFIX']
    }
    
    # Process each line
    updated_lines = []
    updated_keys = set()
    
    for line in lines:
        key = line.split('=')[0].strip() if '=' in line else None
        if key in env_vars:
            updated_lines.append(f"{key}={env_vars[key]}\n")
            updated_keys.add(key)
        else:
            updated_lines.append(line)
    
    # Add any missing variables
    for key, value in env_vars.items():
        if key not in updated_keys:
            updated_lines.append(f"{key}={value}\n")
    
    # Write back to .env
    with open(env_path, 'w') as f:
        f.writelines(updated_lines)
    
    print("Updated .env file with new Cognito configuration")

def create_cognito_user_pool():
    cognito = boto3.client('cognito-idp',
        region_name=os.getenv('AWS_REGION'),
        aws_access_key_id=os.getenv('AWS_ACCESS_KEY_ID'),
        aws_secret_access_key=os.getenv('AWS_SECRET_ACCESS_KEY')
    )

    # Create User Pool with enhanced settings
    user_pool_response = cognito.create_user_pool(
        PoolName='TextileMarketplaceUserPool',
        
        # Allow email as username
        UsernameAttributes=['email'],
        
        # Auto verify email for Google sign-ins
        AutoVerifiedAttributes=['email', 'phone_number'],
        
        # Custom attributes
        Schema=[
            {
                'Name': 'email',
                'AttributeDataType': 'String',
                'Required': True,
                'Mutable': True
            },
            {
                'Name': 'phone_number',
                'AttributeDataType': 'String',
                'Required': False,  # Make phone mandatory
                'Mutable': True
            },
            {
                'Name': 'custom:auth_type',  # Track authentication type
                'AttributeDataType': 'String',
                'Required': False,
                'Mutable': True,
                'StringAttributeConstraints': {
                    'MaxLength': '20',
                    'MinLength': '1'
                }
            }
        ],
        
        # Enable multiple authentication flows
        MfaConfiguration='OPTIONAL',  # Allow OTP-based sign-in
        SmsConfiguration={
            'SnsCallerArn': os.getenv('SNS_ROLE_ARN'),
            'ExternalId': 'textile-marketplace-external-id'
        },

        # Password policy
        Policies={
            'PasswordPolicy': {
                'MinimumLength': 8,
                'RequireUppercase': True,
                'RequireLowercase': True,
                'RequireNumbers': True,
                'RequireSymbols': True
            }
        },

        # SMS verification message
        SmsVerificationMessage='Your verification for Textile Marketplace is {####}',
        
        # Account recovery via phone and email
        AccountRecoverySetting={
            'RecoveryMechanisms': [
                {
                    'Priority': 1,
                    'Name': 'verified_phone_number'
                },
                {
                    'Priority': 2,
                    'Name': 'verified_email'
                }
            ]
        }
    )

    user_pool_id = user_pool_response['UserPool']['Id']

    # Create Google Identity Provider
    cognito.create_identity_provider(
        UserPoolId=user_pool_id,
        ProviderName='Google',
        ProviderType='Google',
        ProviderDetails={
            'client_id': os.getenv('GOOGLE_CLIENT_ID'),
            'client_secret': os.getenv('GOOGLE_CLIENT_SECRET'),
            'authorize_scopes': 'email profile openid',
            'attributes_url': 'https://people.googleapis.com/v1/people/me?personFields=',
            'attributes_url_add_attributes': 'true',
            'authorize_url': 'https://accounts.google.com/o/oauth2/v2/auth',
            'token_url': 'https://oauth2.googleapis.com/token',
            'oidc_issuer': 'https://accounts.google.com'
        },
        
        # Map Google attributes to Cognito attributes
        AttributeMapping={
            'email': 'email',
            'email_verified': 'email_verified',
            'name': 'name',
            'given_name': 'given_name',
            'family_name': 'family_name',
            'phone_number': 'phoneNumbers',
            'custom:auth_type': 'google'  # Mark as Google authentication
        }
    )

    user_pool_id = user_pool_response['UserPool']['Id']
    print(f"Created User Pool: {user_pool_id}")

    # Create domain
    domain_prefix = os.getenv('COGNITO_DOMAIN_PREFIX', 'textile-marketplace')
    try:
        cognito.create_user_pool_domain(
            Domain=domain_prefix,
            UserPoolId=user_pool_id
        )
        print(f"Created domain: {domain_prefix}")
    except Exception as e:
        print(f"Error creating domain: {str(e)}")
        return
    
     # Create App Client with enhanced settings
    client_response = cognito.create_user_pool_client(
        UserPoolId=user_pool_id,
        ClientName='TextileMarketplaceClient',
        GenerateSecret=True,
        
        # Enable all required auth flows
        ExplicitAuthFlows=[
            'ALLOW_CUSTOM_AUTH',           # For OTP login
            'ALLOW_USER_SRP_AUTH',         # For password login
            'ALLOW_REFRESH_TOKEN_AUTH',
            'ALLOW_ADMIN_USER_PASSWORD_AUTH',
            'ALLOW_USER_PASSWORD_AUTH'     # For admin operations
        ],
        
        # OAuth settings for Google
        AllowedOAuthFlows=['code'],
        AllowedOAuthScopes=['email', 'openid', 'profile', 'phone'],
        CallbackURLs=[os.getenv('GOOGLE_REDIRECT_URI', 'http://localhost:3000/api/auth/callback')],
        LogoutURLs=['http://localhost:3000/logout'],
        
        SupportedIdentityProviders=['COGNITO', 'Google'],
        AllowedOAuthFlowsUserPoolClient=True
    )

    client_id = client_response['UserPoolClient']['ClientId']
    client_secret = client_response['UserPoolClient']['ClientSecret']


    # Save configuration
    config = {
        'USER_POOL_ID': user_pool_id,
        'CLIENT_ID': client_id,
        'CLIENT_SECRET': client_secret,
        'DOMAIN_PREFIX': domain_prefix,
        'REGION': os.getenv('AWS_REGION'),
        'DOMAIN': f"{domain_prefix}.auth.{os.getenv('AWS_REGION')}.amazoncognito.com"
    }

    # Update .env file
    update_env_file(config)

    # Save to JSON file for reference
    with open('cognito_config.json', 'w') as f:
        json.dump(config, f, indent=2)

    print("\nCognito Setup Complete!")
    print(f"User Pool ID: {user_pool_id}")
    print(f"Client ID: {client_id}")
    print("Configuration saved to:")
    print("1. cognito_config.json")
    print("2. .env file")
    print(f"\nHosted UI Domain: https://{domain_prefix}.auth.{os.getenv('AWS_REGION')}.amazoncognito.com")
    print("\nTest OAuth URL:")
    print(f"https://{domain_prefix}.auth.{os.getenv('AWS_REGION')}.amazoncognito.com/oauth2/authorize?" + \
          f"client_id={client_id}&" + \
          f"response_type=code&" + \
          f"scope=email+openid+profile&" + \
          f"redirect_uri={os.getenv('GOOGLE_REDIRECT_URI', 'http://localhost:3000/api/auth/callback')}&" + \
          f"identity_provider=Google")

if __name__ == "__main__":
    create_cognito_user_pool()