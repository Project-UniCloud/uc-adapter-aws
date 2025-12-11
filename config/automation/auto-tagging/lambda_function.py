import json
import boto3
import logging
from botocore.exceptions import ClientError

# Logger configuration
logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Client initialization
ec2 = boto3.client('ec2')
s3 = boto3.client('s3')
dynamodb = boto3.client('dynamodb')
iam = boto3.client('iam')
rds = boto3.client('rds')
awslambda = boto3.client('lambda')
elbv2 = boto3.client('elbv2')


def get_user_tags(user_name):
    """Retrieves IAM user tags."""
    try:
        response = iam.list_user_tags(UserName=user_name)
        tags = {t['Key']: t['Value'] for t in response['Tags']}
        return tags
    except Exception as e:
        logger.error(f"Error fetching tags for user {user_name}: {e}")
        return {}


def lambda_handler(event, context):
    try:
        detail = event.get('detail', {})
        user_identity = detail.get('userIdentity', {})
        user_type = user_identity.get('type')

        # Check if the event was triggered by an IAM User
        if user_type == 'IAMUser':
            user_name = user_identity.get('userName')
        else:
            logger.info(f"Event triggered by {user_type}, not IAMUser. Skipping.")
            return

        # 1. Get Group information
        iam_tags = get_user_tags(user_name)
        user_group = iam_tags.get('Group', 'Unknown')

        logger.info(f"Detected user: {user_name} | Group: {user_group}")

        # 2. Prepare tags
        tags_list = [
            {'Key': 'CreatedBy', 'Value': user_name},
            {'Key': 'Group', 'Value': user_group}
        ]

        tags_dict = {
            'CreatedBy': user_name,
            'Group': user_group
        }

        # Event details
        event_source = detail.get('eventSource')
        event_name = detail.get('eventName')
        region = event.get('region')
        account_id = event.get('account')

        response_elements = detail.get('responseElements')

        logger.info(f"Processing: {event_name} in service {event_source}")

        # ==================================================================
        # S3 BUCKETS
        # ==================================================================
        if event_source == 's3.amazonaws.com' and event_name == 'CreateBucket':
            if 'requestParameters' in detail and 'bucketName' in detail['requestParameters']:
                bucket_name = detail['requestParameters']['bucketName']
                logger.info(f"Tagging S3 Bucket: {bucket_name}")

                try:
                    s3.put_bucket_tagging(
                        Bucket=bucket_name,
                        Tagging={'TagSet': tags_list}
                    )
                    logger.info(f"Successfully tagged S3 Bucket: {bucket_name}")
                except ClientError as e:
                    if e.response['Error']['Code'] == 'NoSuchBucket':
                        logger.warning(f"Bucket {bucket_name} no longer exists. Skipping.")
                    else:
                        raise e
            else:
                logger.warning("Missing bucket name in requestParameters.")

        # ==================================================================
        # DYNAMODB
        # ==================================================================
        elif event_source == 'dynamodb.amazonaws.com' and event_name == 'CreateTable':
            if 'requestParameters' in detail and 'tableName' in detail['requestParameters']:
                table_name = detail['requestParameters']['tableName']
                table_arn = f"arn:aws:dynamodb:{region}:{account_id}:table/{table_name}"

                try:
                    dynamodb.tag_resource(
                        ResourceArn=table_arn,
                        Tags=tags_list
                    )
                    logger.info(f"Successfully tagged DynamoDB table: {table_name}")
                except ClientError as e:
                    if e.response['Error']['Code'] == 'ResourceNotFoundException':
                        logger.warning(f"DynamoDB Table {table_name} no longer exists. Skipping.")
                    else:
                        raise e
            else:
                logger.warning("Missing table name in requestParameters.")

        # ==================================================================
        # EC2
        # ==================================================================
        elif event_source == 'ec2.amazonaws.com':
            if not response_elements: return

            resources_to_tag = []

            if event_name == 'RunInstances':
                items = response_elements['instancesSet']['items']
                resources_to_tag = [item['instanceId'] for item in items]
            elif event_name == 'CreateVpc':
                resources_to_tag = [response_elements['vpc']['vpcId']]
            elif event_name == 'CreateSubnet':
                resources_to_tag = [response_elements['subnet']['subnetId']]
            elif event_name == 'CreateNatGateway':
                resources_to_tag = [response_elements['natGateway']['natGatewayId']]
            elif event_name == 'AllocateAddress':
                resources_to_tag = [response_elements['allocationId']]
            elif event_name == 'CreateVolume':
                resources_to_tag = [response_elements['volumeId']]
            elif event_name == 'CreateInternetGateway':
                resources_to_tag = [response_elements['internetGateway']['internetGatewayId']]

            if resources_to_tag:
                try:
                    ec2.create_tags(Resources=resources_to_tag, Tags=tags_list)
                    logger.info(f"Successfully tagged EC2 resources: {resources_to_tag}")
                except ClientError as e:
                    # EC2 rzuca różne błędy (InvalidInstanceID.NotFound, InvalidVpcID.NotFound etc.)
                    # Sprawdzamy czy kod błędu zawiera "NotFound"
                    if 'NotFound' in e.response['Error']['Code']:
                        logger.warning(f"One or more EC2 resources {resources_to_tag} no longer exist. Skipping.")
                    else:
                        raise e

        # ==================================================================
        # LAMBDA
        # ==================================================================
        elif event_source == 'lambda.amazonaws.com' and event_name in ['CreateFunction', 'CreateFunction20150331']:
            if not response_elements: return

            function_name = detail['requestParameters']['functionName']
            function_arn = response_elements['functionArn']

            try:
                awslambda.tag_resource(Resource=function_arn, Tags=tags_dict)
                logger.info(f"Successfully tagged Lambda function: {function_name}")
            except ClientError as e:
                if e.response['Error']['Code'] == 'ResourceNotFoundException':
                    logger.warning(f"Lambda function {function_name} no longer exists. Skipping.")
                else:
                    raise e

        # ==================================================================
        # RDS
        # ==================================================================
        elif event_source == 'rds.amazonaws.com' and event_name == 'CreateDBInstance':
            if not response_elements: return

            db_instance_id = response_elements['dBInstanceIdentifier']
            db_arn = response_elements['dBInstanceArn']

            try:
                rds.add_tags_to_resource(ResourceName=db_arn, Tags=tags_list)
                logger.info(f"Successfully tagged RDS instance: {db_instance_id}")
            except ClientError as e:
                # RDS potrafi rzucić DBInstanceNotFound
                if e.response['Error']['Code'] in ['DBInstanceNotFound', 'ResourceNotFoundFault']:
                    logger.warning(f"RDS Instance {db_instance_id} no longer exists. Skipping.")
                else:
                    raise e

        # ==================================================================
        # LOAD BALANCERS
        # ==================================================================
        elif event_source == 'elasticloadbalancing.amazonaws.com' and event_name == 'CreateLoadBalancer':
            if not response_elements: return

            lbs = response_elements.get('loadBalancers', [])
            if lbs:
                lb_arn = lbs[0]['loadBalancerArn']
                try:
                    elbv2.add_tags(ResourceArns=[lb_arn], Tags=tags_list)
                    logger.info(f"Successfully tagged Load Balancer: {lb_arn}")
                except ClientError as e:
                    if e.response['Error']['Code'] in ['LoadBalancerNotFound', 'ResourceNotFound']:
                        logger.warning(f"Load Balancer {lb_arn} no longer exists. Skipping.")
                    else:
                        raise e

    except Exception as e:
        logger.error(f"CRITICAL ERROR: {e}", exc_info=True)
        # To złapie wszystkie inne błędy (np. brak uprawnień)
        raise e