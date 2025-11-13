import logging

import boto3

# ===========================================
# Pomocnicze funkcje
# ===========================================

def _normalize_name(name: str) -> str:
    char_map = {
        'ą': 'a', 'ć': 'c', 'ę': 'e', 'ł': 'l', 'ń': 'n',
        'ó': 'o', 'ś': 's', 'ź': 'z', 'ż': 'z',
        'Ą': 'A', 'Ć': 'C', 'Ę': 'E', 'Ł': 'L', 'Ń': 'N',
        'Ó': 'O', 'Ś': 'S', 'Ź': 'Z', 'Ż': 'Z',
        ' ': '-', '_': '-'
    }
    for char, replacement in char_map.items():
        name = name.replace(char, replacement)
    return name

def find_resources_by_group(tag_key: str, group_name: str):
    """
    Wyszukuje zasoby AWS posiadające tag o kluczu=tag_key i wartości=group_name.
    Zwraca listę słowników z informacją o typie i identyfikatorze zasobu.
    """
    client = boto3.client("resourcegroupstaggingapi", region_name="us-east-1")
    paginator = client.get_paginator("get_resources")

    resources = []
    for page in paginator.paginate(
            TagFilters=[{"Key": tag_key, "Values": [_normalize_name(group_name)]}]
    ):
        logging.info(page)
        for resource in page.get("ResourceTagMappingList", []):
            arn = resource["ResourceARN"]
            service = arn.split(":")[2]  # np. "ec2", "s3"
            resources.append({"arn": arn, "service": service})
    return resources


def delete_resource(resource):
    """
    Usuwa zasób na podstawie jego ARN i serwisu.
    Obsługuje wybrane typy: EC2 instances, S3 buckets, IAM users.
    """
    arn = resource["arn"]
    service = resource["service"]

    try:
        if service == "ec2":
            ec2 = boto3.resource("ec2", region_name="us-east-1")
            instance_id = arn.split("/")[-1]
            instance = ec2.Instance(instance_id)
            instance.terminate()
            return f"Terminated EC2 instance: {instance_id}"

        elif service == "s3":
            s3 = boto3.resource("s3")
            bucket_name = arn.split(":")[-1]
            bucket = s3.Bucket(bucket_name)
            bucket.objects.all().delete()
            bucket.delete()
            return f"Deleted S3 bucket: {bucket_name}"

        elif service == "iam":
            iam = boto3.resource("iam")
            username = arn.split("/")[-1]
            user = iam.User(username)
            user.delete()
            return f"Deleted IAM user: {username}"

        else:
            return f"Unsupported resource type: {service}"

    except Exception as e:
        return f"Error deleting {arn}: {e}"
