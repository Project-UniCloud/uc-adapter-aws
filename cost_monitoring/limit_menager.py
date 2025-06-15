import boto3

# Create a Cost Explorer client
client = boto3.client('ce')


from datetime import datetime, timezone

def get_total_cost_for_group(group_tag_value: str, start_date: str, end_date: str = None):
    print('Fetching costs for group:', group_tag_value, 'from:', start_date, 'to:', end_date)

    response = client.get_cost_and_usage(
        TimePeriod={
            'Start': "2025-05-01",
            'End': "2025-06-01"
        },
        Granularity='MONTHLY',
        Metrics=['UnblendedCost'],
        GroupBy=[{
            'Type': 'TAG',
            'Key': 'Group'
        }]
    )

    total = 0.0
    for result_by_time in response['ResultsByTime']:
        for group in result_by_time['Groups']:
            tag_key = group['Keys'][0]
            amount = float(group['Metrics']['UnblendedCost']['Amount'])

            if f"Group${group_tag_value}" == tag_key:
                total += amount

    return round(total, 2)


def get_total_costs_for_all_groups(start_date: str, end_date: str = None) -> dict:

    response = client.get_cost_and_usage(
        TimePeriod={
            'Start': start_date,
            'End': end_date,
        },
        Granularity='MONTHLY',
        Metrics=['UnblendedCost'],
        GroupBy=[
            {
                'Type': 'TAG',
                'Key': 'Group'
            }
        ]
    )

    group_costs = {}

    for result_by_time in response['ResultsByTime']:
        for group in result_by_time['Groups']:
            tag_key = group['Keys'][0]
            amount = float(group['Metrics']['UnblendedCost']['Amount'])

            group_name = tag_key.split('$')[1] if '$' in tag_key else tag_key
            group_costs[group_name] = group_costs.get(group_name, 0.0) + amount

    return {group: round(cost, 2) for group, cost in group_costs.items()}


def get_total_aws_cost(start_date: str, end_date: str = None) -> float:

    response = client.get_cost_and_usage(
        TimePeriod={
            'Start': start_date,
            'End': end_date,
        },
        Granularity='MONTHLY',
        Metrics=['UnblendedCost']
    )

    total_cost = 0.0

    for result_by_time in response['ResultsByTime']:
        amount = float(result_by_time['Total']['UnblendedCost']['Amount'])
        total_cost += amount

    return round(total_cost, 2)

