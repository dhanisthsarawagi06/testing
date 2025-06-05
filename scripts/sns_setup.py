import boto3
import json
import os
from dotenv import load_dotenv

load_dotenv()

def create_sns_role():
    """Create IAM role and policy for Cognito SMS"""
    
    iam = boto3.client('iam',
        region_name=os.getenv('AWS_REGION'),
        aws_access_key_id=os.getenv('AWS_ACCESS_KEY_ID'),
        aws_secret_access_key=os.getenv('AWS_SECRET_ACCESS_KEY')
    )

    # Define the trust policy for Cognito
    trust_policy = {
        "Version": "2012-10-17",
        "Statement": [{
            "Effect": "Allow",
            "Principal": {
                "Service": "cognito-idp.amazonaws.com"
            },
            "Action": "sts:AssumeRole"
        }]
    }

    # Define the permission policy for SNS
    sns_policy = {
        "Version": "2012-10-17",
        "Statement": [{
            "Effect": "Allow",
            "Action": [
                "sns:publish"
            ],
            "Resource": "*"
        }]
    }

    try:
        # Create the IAM role
        role_name = 'CognitoSNSRole'
        try:
            role = iam.create_role(
                RoleName=role_name,
                AssumeRolePolicyDocument=json.dumps(trust_policy),
                Description='Role for Cognito to send SMS via SNS'
            )
            print(f"Created IAM role: {role_name}")
        except iam.exceptions.EntityAlreadyExistsException:
            print(f"IAM role {role_name} already exists")
            role = iam.get_role(RoleName=role_name)

        # Create the policy
        policy_name = 'CognitoSNSPolicy'
        try:
            policy = iam.create_policy(
                PolicyName=policy_name,
                PolicyDocument=json.dumps(sns_policy),
                Description='Policy for Cognito SMS permissions'
            )
            policy_arn = policy['Policy']['Arn']
            print(f"Created IAM policy: {policy_name}")
        except iam.exceptions.EntityAlreadyExistsException:
            print(f"IAM policy {policy_name} already exists")
            policy_arn = f"arn:aws:iam::{get_account_id()}:policy/{policy_name}"

        # Attach the policy to the role
        try:
            iam.attach_role_policy(
                RoleName=role_name,
                PolicyArn=policy_arn
            )
            print(f"Attached policy to role")
        except Exception as e:
            print(f"Policy might already be attached: {str(e)}")

        # Configure SNS settings
        sns = boto3.client('sns',
            region_name=os.getenv('AWS_REGION'),
            aws_access_key_id=os.getenv('AWS_ACCESS_KEY_ID'),
            aws_secret_access_key=os.getenv('AWS_SECRET_ACCESS_KEY')
        )

        # Set SMS attributes
        sns.set_sms_attributes(
            attributes={
                'DefaultSMSType': 'Transactional',
                'DefaultSenderID': 'TextileApp'  # Change this to your app name
            }
        )
        print("Configured SNS SMS settings")

        # Save the role ARN to environment file
        role_arn = role['Role']['Arn']
        update_env_file(role_arn)

        return {
            'status': 'success',
            'role_arn': role_arn,
            'policy_arn': policy_arn
        }

    except Exception as e:
        print(f"Error setting up SNS: {str(e)}")
        raise e

def get_account_id():
    """Get AWS account ID"""
    sts = boto3.client('sts',
        region_name=os.getenv('AWS_REGION'),
        aws_access_key_id=os.getenv('AWS_ACCESS_KEY_ID'),
        aws_secret_access_key=os.getenv('AWS_SECRET_ACCESS_KEY')
    )
    return sts.get_caller_identity()['Account']

def update_env_file(role_arn):
    """Update .env file with SNS role ARN"""
    env_path = os.path.join(os.path.dirname(__file__), '..', '.env')
    
    # Read existing contents
    if os.path.exists(env_path):
        with open(env_path, 'r') as f:
            lines = f.readlines()
    else:
        lines = []

    # Update or add SNS_ROLE_ARN
    sns_role_line = f"SNS_ROLE_ARN={role_arn}\n"
    sns_role_found = False

    for i, line in enumerate(lines):
        if line.startswith('SNS_ROLE_ARN='):
            lines[i] = sns_role_line
            sns_role_found = True
            break

    if not sns_role_found:
        lines.append(sns_role_line)

    # Write back to file
    with open(env_path, 'w') as f:
        f.writelines(lines)
    
    print(f"Updated .env file with SNS_ROLE_ARN")

if __name__ == "__main__":
    print("Setting up SNS for Cognito SMS...")
    result = create_sns_role()
    print("\nSNS Setup Complete!")
    print(f"Role ARN: {result['role_arn']}")
    print(f"Policy ARN: {result['policy_arn']}")
    print("\nYou can now run cognito_setup.py to create the user pool with SMS verification")
