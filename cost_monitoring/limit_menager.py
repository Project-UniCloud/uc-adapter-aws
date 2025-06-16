import boto3
from datetime import datetime, timezone
from botocore.exceptions import ClientError

# Create a Cost Explorer client
client = boto3.client('ce')


def get_total_cost_for_group(group_tag_value: str, start_date: str, end_date: str = None) -> float:
    if end_date is None:
        end_date = datetime.now(timezone.utc).strftime('%Y-%m-%d')

    try:
        response = client.get_cost_and_usage(
            TimePeriod={'Start': start_date, 'End': end_date},
            Granularity='MONTHLY',
            Metrics=['UnblendedCost'],
            Filter={
                'Tags': {
                    'Key': 'Group',
                    'Values': [group_tag_value]
                }
            }
        )

        total = sum(
            float(result['Total']['UnblendedCost']['Amount'])
            for result in response['ResultsByTime']
        )
        return round(total, 2)

    except ClientError as error:
        print(f"AWS error while fetching costs for group {group_tag_value}: {error}")
        return 0.0


def get_group_cost_with_service_breakdown(group_tag_value: str, start_date: str, end_date: str = None) -> dict:
    if end_date is None:
        end_date = datetime.now(timezone.utc).strftime('%Y-%m-%d')

    try:
        paginator = client.get_paginator('get_cost_and_usage')
        cost_by_service = {}
        total_cost = 0.0

        for page in paginator.paginate(
                TimePeriod={'Start': start_date, 'End': end_date},
                Granularity='MONTHLY',
                Metrics=['UnblendedCost'],
                Filter={
                    'Tags': {
                        'Key': 'Group',
                        'Values': [group_tag_value]
                    }
                },
                GroupBy=[
                    {'Type': 'DIMENSION', 'Key': 'SERVICE'}
                ]
        ):
            for result_by_time in page['ResultsByTime']:
                for group in result_by_time['Groups']:
                    service_name = group['Keys'][0]
                    amount = float(group['Metrics']['UnblendedCost']['Amount'])

                    if amount <= 0:
                        continue

                    cost_by_service[service_name] = cost_by_service.get(service_name, 0.0) + amount
                    total_cost += amount

        return {
            'total': round(total_cost, 2),
            'by_service': {k: round(v, 2) for k, v in sorted(
                cost_by_service.items(),
                key=lambda item: item[1],
                reverse=True
            )}
        }

    except ClientError as error:
        print(f"AWS error while fetching service breakdown for group {group_tag_value}: {error}")
        return {
            'total': 0.0,
            'by_service': {}
        }

def get_total_costs_for_all_groups(start_date: str, end_date: str = None) -> dict:
    if end_date is None:
        end_date = datetime.now(timezone.utc).strftime('%Y-%m-%d')

    group_costs = {}

    try:
        paginator = client.get_paginator('get_cost_and_usage')
        for page in paginator.paginate(
                TimePeriod={'Start': start_date, 'End': end_date},
                Granularity='MONTHLY',
                Metrics=['UnblendedCost'],
                GroupBy=[{'Type': 'TAG', 'Key': 'Group'}]
        ):
            for result in page['ResultsByTime']:
                for group in result['Groups']:
                    tag_key = group['Keys'][0]

                    if '$' in tag_key:
                        group_name = tag_key.split('$', 1)[1]
                    else:
                        group_name = tag_key

                    cost = float(group['Metrics']['UnblendedCost']['Amount'])
                    group_costs[group_name] = group_costs.get(group_name, 0.0) + cost

        return {group: round(cost, 2) for group, cost in group_costs.items()}

    except ClientError as error:
        print(f"AWS error while fetching group costs: {error}")
        return {}


def get_total_aws_cost(start_date: str, end_date: str = None) -> float:
    if end_date is None:
        end_date = datetime.now(timezone.utc).strftime('%Y-%m-%d')

    try:
        response = client.get_cost_and_usage(
            TimePeriod={'Start': start_date, 'End': end_date},
            Granularity='MONTHLY',
            Metrics=['UnblendedCost']
        )

        total = sum(
            float(result['Total']['UnblendedCost']['Amount'])
            for result in response['ResultsByTime']
        )
        return round(total, 2)

    except ClientError as error:
        print(f"AWS error while fetching total AWS cost: {error}")
        return 0.0