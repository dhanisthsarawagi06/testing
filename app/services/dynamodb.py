from typing import Optional
import boto3
from botocore.exceptions import ClientError
import os
from dotenv import load_dotenv

load_dotenv()

def _get_dynamodb_client():
    return boto3.client(
        'dynamodb',
        endpoint_url=os.getenv('AWS_DYNAMO_ENDPOINT_URL'),
        region_name=os.getenv('AWS_DYNAMO_REGION', 'ap-south-1'),
        aws_access_key_id=os.getenv('AWS_DYNAMO_ACCESS_KEY_ID'),
        aws_secret_access_key=os.getenv('AWS_DYNAMO_SECRET_ACCESS_KEY')
    )

def put_item(table_name: str, item: dict):
    try:
        client = _get_dynamodb_client()
        response = client.put_item(
            TableName=table_name,
            Item=item,
        )
        return response
    except ClientError as e:
        print(f"Error putting item into DynamoDB: {str(e)}")
        raise e

def get_item(table_name: str, key: dict):
    try:
        client = _get_dynamodb_client()
        response = client.get_item(TableName=table_name, Key=key)
        if 'Item' in response:
            return response['Item']
        else:
            return None
    except ClientError as e:
        print(f"Error getting item from DynamoDB: {str(e)}")

        raise e

def get_item_subset(table_name: str, key: dict, projection_expression:str, expression_attribute_names:dict):
    try:
        client = _get_dynamodb_client()
        response = client.get_item(
            TableName=table_name, 
            Key=key,
            ProjectionExpression=projection_expression,
            ExpressionAttributeNames=expression_attribute_names
        )
        return response['Item']
    except ClientError as e:
        print(f"Error getting item from DynamoDB: {str(e)}")
        raise e

def update_item(table_name: str, key: dict, update_expression: str, expression_attribute_names: dict, expression_attribute_values: dict):
    try:
        client = _get_dynamodb_client()
        response = client.update_item(
            TableName=table_name, 
            Key=key, 
            UpdateExpression=update_expression, 
            ExpressionAttributeNames=expression_attribute_names,
            ExpressionAttributeValues=expression_attribute_values,
            ReturnValues = "UPDATED_NEW"
        )
        print(response)
        return response
    except ClientError as e:
        print(f"Error updating item in DynamoDB: {str(e)}")
        raise e

def scan_table(table_name: str):
    try:
        client = _get_dynamodb_client()
        response = client.scan(TableName=table_name)
        return response.get('Items', [])
    except ClientError as e:
        print(f"Error scanning table in DynamoDB: {str(e)}")
        raise e

def query_table(table_name: str, index_name: str = None, key_condition_expression: str = None, expression_attribute_values: dict = None):
    try:
        client = _get_dynamodb_client()
        query_params = {
            'TableName': table_name,
            'KeyConditionExpression': key_condition_expression,
            'ExpressionAttributeValues': expression_attribute_values
        }
        
        if index_name:
            query_params['IndexName'] = index_name
            
        response = client.query(**query_params)
        return response.get('Items', [])
    except ClientError as e:
        print(f"Error querying table in DynamoDB: {str(e)}")
        raise e
    
def query(table_name: str, key_condition_expression: str, expression_attribute_names: dict, expression_attribute_values: dict, scan_index_forward: bool = False, filter_expression: str = "", index_name: Optional[str] = None):
    try:
        client = _get_dynamodb_client()
        if index_name is not None:
            if filter_expression == "":
                response = client.query(
                    TableName=table_name, 
                    IndexName=index_name, 
                    KeyConditionExpression=key_condition_expression, 
                    ExpressionAttributeNames=expression_attribute_names, 
                    ExpressionAttributeValues=expression_attribute_values,
                    ScanIndexForward=scan_index_forward
                )
            else:
                response = client.query(
                    TableName=table_name, 
                    IndexName=index_name, 
                    KeyConditionExpression=key_condition_expression, 
                    ExpressionAttributeNames=expression_attribute_names, 
                    ExpressionAttributeValues=expression_attribute_values,
                    FilterExpression=filter_expression,
                    ScanIndexForward=scan_index_forward
                )
        else:
            if filter_expression == "":
                response = client.query(
                    TableName=table_name, 
                    KeyConditionExpression=key_condition_expression, 
                    ExpressionAttributeNames=expression_attribute_names, 
                    ExpressionAttributeValues=expression_attribute_values,
                    ScanIndexForward=scan_index_forward
                )
            else:
                response = client.query(
                    TableName=table_name, 
                    KeyConditionExpression=key_condition_expression, 
                    ExpressionAttributeNames=expression_attribute_names, 
                    ExpressionAttributeValues=expression_attribute_values,
                    FilterExpression=filter_expression,
                    ScanIndexForward=scan_index_forward
                )
        return response['Items']
    except ClientError as e:
        print(f"Error querying DynamoDB: {str(e)}")
        raise e

def scan(table_name: str, projection_expression: str, filter_expression: str = ""):
    try:
        client = _get_dynamodb_client()
        response = client.scan(
            TableName=table_name,
            ProjectionExpression=projection_expression,
            FilterExpression=filter_expression
        )
        return response.get('Items', [])
    except ClientError as e:
        print(f"Error scanning table in DynamoDB: {str(e)}")
        raise e

def delete_item(table_name: str, key: dict, condition_expression: str = None):
    try:
        client = _get_dynamodb_client()
        
        params = {
            'TableName': table_name,
            'Key': key
        }
        
        if condition_expression:
            params['ConditionExpression'] = condition_expression
            
        response = client.delete_item(**params)
        return response
    except ClientError as e:
        print(f"Error deleting item from DynamoDB: {str(e)}")
        raise e

