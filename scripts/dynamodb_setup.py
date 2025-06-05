import boto3
from dotenv import load_dotenv
import os

load_dotenv()

DYNAMODB_USER_TABLE = os.getenv('DYNAMODB_USER_TABLE')
DYNAMODB_DESIGN_TABLE = os.getenv('DYNAMODB_DESIGN_TABLE')
DYNAMODB_TRANSACTION_TABLE = os.getenv('DYNAMODB_TRANSACTION_TABLE')
DYNAMODB_COLLECTION_TABLE = os.getenv('DYNAMODB_COLLECTION_TABLE')

class DynamoDBSetup():
    def __init__(self):

        self.client = boto3.client(
            'dynamodb', 
            endpoint_url=os.getenv('AWS_DYNAMO_ENDPOINT_URL'),
            region_name=os.getenv('AWS_DYNAMO_REGION'),
            aws_access_key_id=os.getenv('AWS_DYNAMO_ACCESS_KEY_ID'),
            aws_secret_access_key=os.getenv('AWS_DYNAMO_SECRET_ACCESS_KEY')
        )
    
    def CreateUserTable(self):
        table = self.client.create_table(
                    TableName=DYNAMODB_USER_TABLE,
                    AttributeDefinitions=[
                        # {
                        #     'AttributeName': 'user_id',
                        #     'AttributeType': 'S'
                        # },
                        {

                            'AttributeName': 'email',
                            'AttributeType': 'S'
                        },
                        {
                            'AttributeName': 'referral_code',
                            'AttributeType': 'S'
                        }
                    ],
                    KeySchema=[
                        {
                            'AttributeName': 'email',
                            'KeyType': 'HASH'
                        }
                    ],
                    # LocalSecondaryIndexes=[
                    #     {
                    #         'IndexName': 'UserEmailGSI',
                    #         'KeySchema': [
                    #             {
                    #                 'AttributeName': 'email',
                    #                 'KeyType': 'HASH'
                    #             }
                    #         ],
                    #         'Projection': {
                    #             'ProjectionType': 'ALL'
                    #         }
                    #     }
                    # ],
                    GlobalSecondaryIndexes=[
                        {
                            'IndexName': 'UserReferralGSI',
                            'KeySchema': [
                                {
                                    'AttributeName': 'referral_code',
                                    'KeyType': 'HASH'
                                }
                            ],
                            'Projection': {
                                'ProjectionType': 'KEYS_ONLY'
                            }
                        }
                    ],
                    BillingMode='PROVISIONED',
                    ProvisionedThroughput={
                        'ReadCapacityUnits': 20,
                        'WriteCapacityUnits': 20
                    },
                    TableClass='STANDARD',
                    DeletionProtectionEnabled=True,
                )
        # table.wait_until_exists()
    
    def CreateDesignTable(self):
        table = self.client.create_table(
            TableName=DYNAMODB_DESIGN_TABLE,
            AttributeDefinitions=[
                {

                    'AttributeName': 'design_id',
                    'AttributeType': 'S'
                },
                {
                    'AttributeName': 'seller_email',
                    'AttributeType': 'S'
                },
                {
                    'AttributeName': 'category',
                    'AttributeType': 'S'
                },
                {
                    'AttributeName': 'verification_status',
                    'AttributeType': 'S'
                }
            ],
            KeySchema=[
                {
                    'AttributeName': 'design_id',
                    'KeyType': 'HASH'
                }
            ],
            GlobalSecondaryIndexes=[
                {
                    'IndexName': 'DesignSellerGSI',
                    'KeySchema': [
                        {
                            'AttributeName': 'seller_email',
                            'KeyType': 'HASH'
                        }
                    ],
                    'Projection': {
                        'ProjectionType': 'ALL'
                    },
                    'ProvisionedThroughput': {
                        'ReadCapacityUnits': 10,
                        'WriteCapacityUnits': 10
                    }
                },
                {
                    'IndexName': 'category-status-index',
                    'KeySchema': [
                        {
                            'AttributeName': 'category',
                            'KeyType': 'HASH'
                        },
                        {
                            'AttributeName': 'verification_status',
                            'KeyType': 'RANGE'
                        }
                    ],
                    'Projection': {
                        'ProjectionType': 'ALL'
                    },
                    'ProvisionedThroughput': {
                        'ReadCapacityUnits': 10,
                        'WriteCapacityUnits': 10
                    }
                }
            ],
            BillingMode='PAY_PER_REQUEST',
            TableClass='STANDARD',
            DeletionProtectionEnabled=True,
            OnDemandThroughput={
                'MaxReadRequestUnits': 50,
                'MaxWriteRequestUnits': 20
            }
        )
        # table.wait_until_exists()
    
    def CreateTransactionTable(self):
        table = self.client.create_table(
            TableName=DYNAMODB_TRANSACTION_TABLE,
            AttributeDefinitions=[
                {

                    'AttributeName': 'transaction_id',
                    'AttributeType': 'S'
                },
                {
                    'AttributeName': 'buyer_email',
                    'AttributeType': 'S'
                }
            ],
            KeySchema=[
                {
                    'AttributeName': 'transaction_id',
                    'KeyType': 'HASH'
                }
            ],
            GlobalSecondaryIndexes=[
                {
                    'IndexName': 'buyer_email-index',
                    'KeySchema': [
                        {
                            'AttributeName': 'buyer_email',
                            'KeyType': 'HASH'
                        }
                    ],
                    'Projection': {
                        'ProjectionType': 'ALL'
                    },
                    'ProvisionedThroughput': {
                        'ReadCapacityUnits': 10,
                        'WriteCapacityUnits': 10
                    }
                }
            ],
            BillingMode='PROVISIONED',
            ProvisionedThroughput={
                'ReadCapacityUnits': 10,
                'WriteCapacityUnits': 10
            },
            TableClass='STANDARD',
            DeletionProtectionEnabled=True,
        )
        # table.wait_until_exists()
    
    def CreateCollectionTable(self):
        table = self.client.create_table(
                    TableName=DYNAMODB_COLLECTION_TABLE,
                    AttributeDefinitions=[
                        {
                            'AttributeName': 'user_email',
                            'AttributeType': 'S'
                        },
                        {
                            'AttributeName': 'collection_name',
                            'AttributeType': 'S'
                        }
                    ],
                    KeySchema=[
                        {
                            'AttributeName': 'user_email',
                            'KeyType': 'HASH'
                        },
                        {
                            'AttributeName': 'collection_name',
                            'KeyType': 'RANGE'
                        }
                    ],
                    BillingMode='PAY_PER_REQUEST',
                    TableClass='STANDARD',
                    DeletionProtectionEnabled=True
                )
        # table.wait_until_exists()

    def disable_deletion_protection(self, table_name: str):
        self.client.update_table(
            TableName=table_name,
            DeletionProtectionEnabled=False
        )

def main():
    try:
        dynamodb_setup = DynamoDBSetup()
        # dynamodb_setup.CreateUserTable()
        # dynamodb_setup.CreateDesignTable()
        # dynamodb_setup.CreateTransactionTable()
        dynamodb_setup.CreateCollectionTable()
        # dynamodb_setup.disable_deletion_protection('User')
        # dynamodb_setup.disable_deletion_protection('Design')
        # dynamodb_setup.disable_deletion_protection('Transaction')
        # dynamodb_setup.disable_deletion_protection('Collection')
    except Exception as e:
        print(f"Error: {e}")

if __name__ == '__main__':
    main()