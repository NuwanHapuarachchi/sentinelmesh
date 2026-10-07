"""Read-only teardown check: exits 1 if anything SentinelMesh is still in the account.

    python scripts/verify_teardown.py --stack app        # after an app destroy
    python scripts/verify_teardown.py --stack bootstrap  # after the final bootstrap destroy

It never deletes anything; Terraform owns all deletion.
"""

import argparse
import sys
import time

import boto3

PREFIX = "sentinelmesh"
REGION = "ap-southeast-2"


def by_prefix(names):
    return [n for n in names if n and n.lower().startswith(PREFIX)]


def app_leftovers():
    found = []
    s = boto3.session.Session(region_name=REGION)

    tagged = s.client("resourcegroupstaggingapi").get_resources(
        TagFilters=[{"Key": "Project", "Values": ["SentinelMesh"]}, {"Key": "Stack", "Values": ["app"]}]
    )["ResourceTagMappingList"]
    found += [f"tagged: {r['ResourceARN']}" for r in tagged]

    found += [f"lambda: {n}" for n in by_prefix(f["FunctionName"] for f in s.client("lambda").list_functions()["Functions"])]
    found += [f"dynamodb: {n}" for n in by_prefix(s.client("dynamodb").list_tables()["TableNames"])]
    found += [f"s3: {n}" for n in by_prefix(b["Name"] for b in s.client("s3").list_buckets()["Buckets"])]
    logs = s.client("logs")
    for lg_prefix in (f"/aws/lambda/{PREFIX}", f"/{PREFIX}", f"/aws/vendedlogs/states/{PREFIX}", f"aws-waf-logs-{PREFIX}"):
        groups = logs.describe_log_groups(logGroupNamePrefix=lg_prefix)["logGroups"]
        found += [f"log group: {g['logGroupName']}" for g in groups]
    iot = s.client("iot")
    found += [f"iot rule: {n}" for n in by_prefix(r["ruleName"] for r in iot.list_topic_rules()["rules"])]
    found += [f"iot thing: {n}" for n in by_prefix(t["thingName"] for t in iot.list_things()["things"])]
    found += [f"iot policy: {n}" for n in by_prefix(p["policyName"] for p in iot.list_policies()["policies"])]
    found += [f"states: {n}" for n in by_prefix(m["name"] for m in s.client("stepfunctions").list_state_machines()["stateMachines"])]
    found += [f"sqs: {u}" for u in s.client("sqs").list_queues(QueueNamePrefix=PREFIX).get("QueueUrls", [])]
    found += [f"sns: {t['TopicArn']}" for t in s.client("sns").list_topics()["Topics"] if f":{PREFIX}" in t["TopicArn"]]
    found += [f"cognito: {n}" for n in by_prefix(p["Name"] for p in s.client("cognito-idp").list_user_pools(MaxResults=60)["UserPools"])]
    found += [f"apigateway: {n}" for n in by_prefix(a["name"] for a in s.client("apigateway").get_rest_apis()["items"])]
    found += [f"waf: {n}" for n in by_prefix(a["Name"] for a in s.client("wafv2").list_web_acls(Scope="REGIONAL")["WebACLs"])]
    found += [f"schedule: {n}" for n in by_prefix(x["Name"] for x in s.client("scheduler").list_schedules()["Schedules"])]
    found += [f"secret: {n}" for n in by_prefix(x["Name"] for x in s.client("secretsmanager").list_secrets()["SecretList"])]
    found += [f"athena: {n}" for n in by_prefix(w["Name"] for w in s.client("athena").list_work_groups()["WorkGroups"])]
    found += [f"glue db: {n}" for n in by_prefix(d["Name"] for d in s.client("glue").get_databases()["DatabaseList"])]
    distributions = s.client("cloudfront").list_distributions().get("DistributionList", {}).get("Items", [])
    found += [f"cloudfront: {d['Id']}" for d in distributions if PREFIX in d.get("Comment", "").lower()]
    found += [f"iam role: {r['RoleName']}" for r in s.client("iam").list_roles(PathPrefix=f"/{PREFIX}/")["Roles"]]
    return found


def bootstrap_leftovers():
    iam = boto3.client("iam")
    found = []
    found += [f"iam user: {u['UserName']}" for u in iam.list_users()["Users"] if u["UserName"].startswith(PREFIX)]
    found += [f"iam role: {r['RoleName']}" for r in iam.list_roles()["Roles"] if r["RoleName"].startswith(PREFIX)]
    found += [f"iam policy: {p['PolicyName']}" for p in iam.list_policies(Scope="Local")["Policies"] if p["PolicyName"].startswith(PREFIX)]
    return found + app_leftovers()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--stack", choices=["app", "bootstrap"], required=True)
    args = parser.parse_args()
    check = app_leftovers if args.stack == "app" else bootstrap_leftovers

    # The tagging API is eventually consistent; give deletions a moment to settle.
    for attempt in range(6):
        leftovers = check()
        if not leftovers:
            print(f"teardown verified: no SentinelMesh resources remain ({args.stack} stack)")
            return 0
        print(f"attempt {attempt + 1}: {len(leftovers)} resource(s) still present, rechecking in 20s")
        time.sleep(20)

    print("TEARDOWN INCOMPLETE:")
    for item in leftovers:
        print(f"  {item}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
