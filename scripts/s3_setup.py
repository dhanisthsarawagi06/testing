import boto3
import json
import os
from botocore.exceptions import ClientError
import dotenv

dotenv.load_dotenv()

def create_s3_buckets():
    try:
        s3 = boto3.client('s3',
            aws_access_key_id=os.getenv('AWS_ACCESS_KEY_ID'),
            aws_secret_access_key=os.getenv('AWS_SECRET_ACCESS_KEY'),
            region_name=os.getenv('AWS_REGION')
        )
        
        # Bucket configurations
        buckets = {
            'textile-marketplace-designs': {
                'cors': [
                    {
                        'AllowedHeaders': ['*'],
                        'AllowedMethods': ['GET', 'PUT', 'POST', 'DELETE'],
                        'AllowedOrigins': ['http://localhost:3000', 'http://localhost:8000', 'https://www.textile-designer.shop'],
                        'ExposeHeaders': ['ETag'],
                        'MaxAgeSeconds': 3000
                    }
                ],
                'policy': {
                    'Version': '2012-10-17',
                    'Statement': [
                        {
                            'Sid': 'PublicReadForDesignsGet',
                            'Effect': 'Allow',
                            'Principal': '*',
                            'Action': ['s3:GetObject'],
                            'Resource': f'arn:aws:s3:::textile-marketplace-designs/*'
                        }
                    ]
                }
            },
            'textile-marketplace-thumbnails': {
                'cors': [
                    {
                        'AllowedHeaders': ['*'],
                        'AllowedMethods': ['GET'],
                        'AllowedOrigins': ['http://localhost:3000', 'https://localhost:8000', 'https://www.textile-designer.shop'],
                        'ExposeHeaders': ['ETag'],
                        'MaxAgeSeconds': 3000
                    }
                ],
                'policy': {
                    'Version': '2012-10-17',
                    'Statement': [
                        {
                            'Sid': 'PublicReadForThumbnailsGet',
                            'Effect': 'Allow',
                            'Principal': '*',
                            'Action': ['s3:GetObject'],
                            'Resource': f'arn:aws:s3:::textile-marketplace-thumbnails/*'
                        }
                    ]
                }
            }
        }

        # Create buckets and configure them
        for bucket_name, config in buckets.items():
            try:
                # Create bucket with location constraint
                if os.getenv('AWS_REGION'):
                    s3.create_bucket(
                        Bucket=bucket_name,
                        CreateBucketConfiguration={
                            'LocationConstraint': os.getenv('AWS_REGION')
                        }
                    )
                else:
                    s3.create_bucket(Bucket=bucket_name)
                print(f"Created bucket: {bucket_name}")

                # Disable block public access settings
                s3.put_public_access_block(
                    Bucket=bucket_name,
                    PublicAccessBlockConfiguration={
                        'BlockPublicAcls': False,
                        'IgnorePublicAcls': False,
                        'BlockPublicPolicy': False,
                        'RestrictPublicBuckets': False
                    }
                )
                print(f"Disabled block public access for bucket: {bucket_name}")

                # Configure CORS
                s3.put_bucket_cors(
                    Bucket=bucket_name,
                    CORSConfiguration={'CORSRules': config['cors']}
                )
                print(f"Set CORS for bucket: {bucket_name}")

                # Set bucket policy
                policy_json = json.dumps(config['policy'])
                s3.put_bucket_policy(
                    Bucket=bucket_name,
                    Policy=policy_json
                )
                print(f"Set policy for bucket: {bucket_name}")

                # Enable versioning (optional but recommended)
                s3.put_bucket_versioning(
                    Bucket=bucket_name,
                    VersioningConfiguration={'Status': 'Enabled'}
                )
                print(f"Enabled versioning for bucket: {bucket_name}")

            except ClientError as e:
                print(f"Error with bucket {bucket_name}: {str(e)}")
                raise

    except Exception as e:
        print(f"Error setting up S3 buckets: {str(e)}")
        raise

def main():
    create_s3_buckets()

if __name__ == "__main__":
    main()

