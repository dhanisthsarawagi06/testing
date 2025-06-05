import boto3
from botocore.exceptions import ClientError
import os
import dotenv
import logging

dotenv.load_dotenv()

logger = logging.getLogger(__name__)

class S3Handler:
    def __init__(self):
        self.s3_client = boto3.client(
            's3',
            # aws_access_key_id=os.getenv('AWS_ACCESS_KEY_ID'),
            # aws_secret_access_key=os.getenv('AWS_SECRET_ACCESS_KEY'),
            region_name=os.getenv('AWS_REGION')
        )
        self.designs_bucket = os.getenv('S3_DESIGN_BUCKET_NAME')
        self.thumbnails_bucket = os.getenv('S3_THUMBNAIL_BUCKET_NAME')

    async def generate_presigned_url(self, bucket_name: str, object_name: str, expiration=3600):
        """Generate a presigned URL to share an S3 object"""
        try:
            # Extract filename from object_name
            filename = object_name.split('/')[-1]
            
            url = self.s3_client.generate_presigned_url(
                'get_object',
                Params={
                    'Bucket': bucket_name,
                    'Key': object_name,
                    'ResponseContentDisposition': f'attachment; filename="{filename}"',
                    'ResponseContentType': 'application/octet-stream'
                },
                ExpiresIn=expiration
            )
            return url
        except ClientError as e:
            logger.error(f"Error generating presigned URL: {e}")
            return None


    async def upload_file(self, file_data: bytes, bucket: str, object_name: str):
        """Upload a file to S3"""
        try:
            self.s3_client.put_object(
                Bucket=bucket,
                Key=object_name,
                Body=file_data
            )
            return True
        except ClientError as e:
            logger.error(f"Error uploading file to S3: {e}")
            return False

    async def delete_file(self, bucket: str, key: str) -> bool:
        """Delete a file from S3 bucket"""
        try:
            self.s3_client.delete_object(
                Bucket=bucket,
                Key=key
            )
            return True
        except Exception as e:
            print(f"Error deleting file from S3: {str(e)}")
            return False

    async def generate_presigned_post(self, bucket_name: str, object_name: str, content_type: str):
        try:
            conditions = [
                {"Content-Type": content_type}
            ]
            
            response = self.s3_client.generate_presigned_post(
                Bucket=bucket_name,
                Key=object_name,
                Conditions=conditions,
                ExpiresIn=3600
            )
            return response
        except ClientError as e:
            logger.error(f"Error generating presigned POST URL: {e}")
            return None