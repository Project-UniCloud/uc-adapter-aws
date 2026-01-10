import boto3
import logging
from botocore.exceptions import ClientError
from common.naming import normalize_name

# Setup logger for this module
logger = logging.getLogger(__name__)

# Stała dla regionu (opcjonalnie można przenieść do configu)
AWS_REGION = "us-east-1"


def find_resources_by_group(tag_key: str, group_name: str):
    """
    Searches for AWS resources having a specific tag key and value (group name).
    Uses the Resource Groups Tagging API.
    """
    normalized_group = normalize_name(group_name)
    logger.info(f"Searching resources with Tag: {tag_key}={normalized_group}")

    client = boto3.client("resourcegroupstaggingapi", region_name=AWS_REGION)
    paginator = client.get_paginator("get_resources")

    resources = []
    try:
        for page in paginator.paginate(
                TagFilters=[{"Key": tag_key, "Values": [normalized_group]}]
        ):
            for resource in page.get("ResourceTagMappingList", []):
                arn = resource["ResourceARN"]
                # ARN format: arn:aws:service:region:account:resource_id
                try:
                    # Extract service name (e.g., 'ec2', 's3')
                    service = arn.split(":")[2]
                    resources.append({"resource_global_id": arn, "service": service})
                except IndexError:
                    logger.warning(f"Could not parse service from ARN: {arn}")

    except Exception as e:
        logger.error(f"Error searching for resources: {e}")
        raise e

    return resources


def delete_resource(resource):
    """
    Deletes a specific AWS resource based on ARN and service type.
    Supports: EC2 (Instances, NAT, EIP, Volumes), S3, Lambda, DynamoDB, RDS, etc.
    """
    arn = resource.get("resource_global_id")
    service = resource.get("service")
    msg = ""

    try:
        # --- COMPUTE & NETWORK (EC2 Family) ---
        if service == "ec2":
            ec2_res = boto3.resource("ec2", region_name=AWS_REGION)
            ec2_client = boto3.client("ec2", region_name=AWS_REGION)

            if "instance/" in arn:
                instance_id = arn.split("/")[-1]
                try:
                    ec2_client.terminate_instances(InstanceIds=[instance_id])
                    msg = f"🔌 Terminated EC2 instance: {instance_id}"
                except ClientError as e:
                    if "InvalidInstanceID.NotFound" in str(e):
                        return f"Already deleted: {instance_id}"
                    raise e

            elif "natgateway/" in arn:
                nat_id = arn.split("/")[-1]
                ec2_client.delete_nat_gateway(NatGatewayId=nat_id)
                msg = f"💸 Deleted NAT Gateway: {nat_id}"

            elif "volume/" in arn:
                vol_id = arn.split("/")[-1]
                ec2_client.delete_volume(VolumeId=vol_id)
                msg = f"💿 Deleted EBS Volume: {vol_id}"

            else:
                msg = f"   Skipping other EC2 resource type: {arn}"

        # --- STORAGE ---
        elif service == "s3":
            s3 = boto3.resource("s3")
            bucket_name = arn.split(":")[-1]
            bucket = s3.Bucket(bucket_name)
            try:
                # S3 requires emptying the bucket before deletion
                bucket.objects.all().delete()
                bucket.object_versions.all().delete()
                bucket.delete()
                msg = f"🗑️ Deleted S3 bucket: {bucket_name}"
            except ClientError as e:
                if "NoSuchBucket" in str(e):
                    return f"Already deleted: {bucket_name}"
                raise e

        # --- SERVERLESS ---
        elif service == "lambda":
            client = boto3.client("lambda", region_name=AWS_REGION)
            func_name = arn.split(":")[-1]
            client.delete_function(FunctionName=func_name)
            msg = f"🔥 Deleted Lambda: {func_name}"

        # --- DATABASES ---
        elif service == "dynamodb":
            client = boto3.client("dynamodb", region_name=AWS_REGION)
            table_name = arn.split("/")[-1]
            client.delete_table(TableName=table_name)
            msg = f"📉 Deleted DynamoDB table: {table_name}"

        elif service == "rds":
            client = boto3.client("rds", region_name=AWS_REGION)
            if ":db:" in arn:
                db_id = arn.split(":")[-1]
                client.delete_db_instance(
                    DBInstanceIdentifier=db_id,
                    SkipFinalSnapshot=True,
                    DeleteAutomatedBackups=True
                )
                msg = f"🗄️ Deleting RDS instance: {db_id}"
            else:
                msg = f"   Skipping RDS non-instance resource: {arn}"

        # --- NETWORKING & INTEGRATION ---
        elif service == "elasticloadbalancing":
            client = boto3.client("elbv2", region_name=AWS_REGION)
            client.delete_load_balancer(LoadBalancerArn=arn)
            msg = f"⚖️ Deleted Load Balancer: {arn.split('/')[-1]}"

        elif service == "sqs":
            client = boto3.client("sqs", region_name=AWS_REGION)
            q_name = arn.split(":")[-1]
            try:
                q_url = client.get_queue_url(QueueName=q_name)['QueueUrl']
                client.delete_queue(QueueUrl=q_url)
                msg = f"📨 Deleted SQS Queue: {q_name}"
            except ClientError:
                msg = f"   SQS Queue {q_name} not found or access denied."

        elif service == "sns":
            client = boto3.client("sns", region_name=AWS_REGION)
            client.delete_topic(TopicArn=arn)
            msg = f"📣 Deleted SNS Topic: {arn.split(':')[-1]}"

        # --- MANAGEMENT ---
        elif service == "logs":
            client = boto3.client("logs", region_name=AWS_REGION)
            try:
                # Log Group ARN ends with :* usually
                log_group = arn.split("log-group:")[-1].split(":")[0]
                client.delete_log_group(LogGroupName=log_group)
                msg = f"📜 Deleted Log Group: {log_group}"
            except Exception:
                msg = f"   Could not parse Log Group ARN."

        elif service == "apigateway":
            client = boto3.client("apigateway", region_name=AWS_REGION)
            if "/restapis/" in arn:
                api_id = arn.split("/restapis/")[1].split("/")[0]
                client.delete_rest_api(restApiId=api_id)
                msg = f"🌐 Deleted API Gateway (REST): {api_id}"
            else:
                msg = f"   Skipping unknown API Gateway resource."

        else:
            msg = f"   ℹ️ Unknown service type for cleaner: {service}"

        logger.info(msg)
        return msg

    except ClientError as e:
        code = e.response['Error']['Code']
        if code in ['ResourceNotFoundException', 'NoSuchEntity', 'NotFound', 'InvalidInstanceID.NotFound']:
            return f"Already deleted: {arn}"

        err = f"❌ AWS Error deleting {arn}: {e}"
        logger.error(err)
        return err

    except Exception as e:
        err = f"❌ General Error deleting {arn}: {e}"
        logger.error(err)
        return err


def get_group_resources_details(group_name: str) -> list[dict]:
    """
    Retrieves a detailed, human-readable list of resources associated with a group.
    Uses AWS Resource Groups Tagging API.
    """
    normalized_group = normalize_name(group_name)

    logger.info(f"🔍 Fetching resources for tag Group={normalized_group}")

    tagging_client = boto3.client('resourcegroupstaggingapi', region_name=AWS_REGION)

    try:
        response = tagging_client.get_resources(
            TagFilters=[
                {'Key': 'Group', 'Values': [normalized_group]}
            ]
        )
    except Exception as e:
        logger.error(f"❌ Failed to fetch resources from AWS: {e}")
        return []

    readable_resources = []

    for item in response.get('ResourceTagMappingList', []):
        arn = item['ResourceARN']
        tags = {t['Key']: t['Value'] for t in item.get('Tags', [])}

        # Parse ARN to extract service and resource ID
        arn_parts = arn.split(':')
        service = arn_parts[2] if len(arn_parts) > 2 else "unknown"

        # Extract ID (after the last slash usually, or last colon)
        resource_id = arn_parts[-1].split('/')[-1]

        resource_data = {
            "resource_global_id": arn,
            "service": service,
            "resource_id": resource_id,
            "name": tags.get('Name', 'N/A'),
            "created_by": tags.get('CreatedBy', tags.get('User', 'Unknown')),
            "type": _guess_resource_type(arn)  # Called as a standalone function
        }

        readable_resources.append(resource_data)

    return readable_resources


def _guess_resource_type(arn: str) -> str:
    """Helper function to determine a friendly resource type from the ARN."""
    # Compute
    if ":instance/" in arn: return "EC2 Instance"
    if ":volume/" in arn: return "EBS Volume"
    if ":function:" in arn: return "Lambda Function"

    # Storage & DB
    if ":bucket/" in arn or "arn:aws:s3:::" in arn: return "S3 Bucket"
    if ":table/" in arn: return "DynamoDB Table"
    if ":db:" in arn: return "RDS Instance"

    # Messaging
    if ":queue/" in arn: return "SQS Queue"
    if ":topic:" in arn: return "SNS Topic"

    # Security & IAM
    if ":user/" in arn: return "IAM User"
    if ":role/" in arn: return "IAM Role"
    if ":policy/" in arn: return "IAM Policy"
    if ":security-group/" in arn: return "Security Group"
    if ":vpc/" in arn: return "VPC"

    return "Generic Resource"