"""
This extension sends a notification to a specific slack (dm) channel
"""
import requests
import json
from typing import List, Optional


def rossum_hook_request_handler(payload: dict) -> dict:
    
    messages = []
    operations = []

    #general settings   
    slackuser = payload["settings"]["slack_user_email"]
    slacktoken = payload["secrets"]["slack_token"]
    base_url = payload["base_url"]
    rossum_token = payload["rossum_authorization_token"]
    
    #annotation data    
    status = payload["annotation"]["status"]
    annotation_id = payload["annotation"]["id"]
    annotation_content_url = payload["annotation"]["content"]

    # get annoation data
    annotation_content = get_annotation_content(annotation_content_url, rossum_token)
  
    # check if status is to_review, only then sent slack notification
    if status == "in_workflow":
        user = find_slackuser_by_email(slackuser, slacktoken)
        send_slack_message(user, slacktoken, annotation_id, base_url, annotation_content["content"])
        
    
    messages = [create_message("info", "slack notification sent")]
    return {"messages": messages, "operations": operations}


def find_slackuser_by_email(slackuser, slacktoken):
    
    url = "https://slack.com/api/users.lookupByEmail?email=" + slackuser
    header = {"Authorization": "Bearer " + slacktoken}
    response = requests.get(url, headers=header)

    return response.json()["user"]
    
    

def send_slack_message(user, slacktoken, annotation_id, base_url, annotation_content):
    
    userid = user["id"]
    realname = user["real_name"]
    
    # Extract invoice information
    invoicenumber = find_by_schema_id(annotation_content, "document_id")["content"]["value"]
    vendorname = find_by_schema_id(annotation_content, "sender_name")["content"]["value"]
    duedate = find_by_schema_id(annotation_content, "date_due")["content"]["value"]
    amount_total = find_by_schema_id(annotation_content, "amount_total")["content"]["value"]
    currency = find_by_schema_id(annotation_content, "currency")["content"]["value"].upper()

    # Get document URL for direct access
    document_url = base_url + "/requests/" + str(annotation_id)
  
    # Prepare Slack message
    blocks =  [
            {
                "type": "header",
                "text": {
                    "type": "plain_text",
                    "text": "📋 Invoice Approval Required"
                }
            },
            {
                "type": "section",
                "fields": [
                    {
                        "type": "mrkdwn",
                        "text": f"*Assigned to:*\n{realname}"
                    },
                    {
                        "type": "mrkdwn",
                        "text": "*Status:*\nPending Approval"
                    }
                ]
            },
            {
                "type": "divider"
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": "*Invoice Details:*"
                }
            },
            {
                "type": "section",
                "fields": [
                    {
                        "type": "mrkdwn",
                        "text": f"*Vendor:*\n{vendorname}"
                    },
                    {
                        "type": "mrkdwn",
                        "text": f"*Invoice Number:*\n{invoicenumber}"
                    },
                    {
                        "type": "mrkdwn",
                        "text": f"*Amount:*\n{amount_total} {currency}"
                    },
                    {
                        "type": "mrkdwn",
                        "text": f"*Due Date:*\n{duedate}"
                    }
                ]
            },
            {
                "type": "actions",
                "elements": [
                    {
                        "type": "button",
                        "text": {
                            "type": "plain_text",
                            "text": "Review Invoice"
                        },
                        "style": "primary",
                        "url": document_url
                    }
                ]
            }
        ]
    

    url = "https://slack.com/api/chat.postMessage"
    header = {"Authorization": "Bearer " + slacktoken}
    formdata = {"channel": userid, "text":"An invoice requires your approval", "blocks": json.dumps(blocks)}
    
    response = requests.post(url, headers=header, data=formdata)
    return


    
def create_message(message_type, message_content, datapoint_id=None):
    return {
        "content": message_content,
        "type": message_type,
        "id": datapoint_id,
    }


def get_annotation_content(annotationurl: str, rossum_token: str) -> dict:
    url = annotationurl
    header = {"Authorization": "Bearer " + rossum_token}
    response = requests.get(url, headers=header)

    return response.json()
    
    

def find_by_schema_id(content, schema_id: str) -> Optional[dict]:
    """
    Return the first datapoint matching a schema id or None if no such element exists.
    :param content: annotation content tree (see https://api.elis.rossum.ai/docs/#annotation-data)
    :param schema_id: field's ID as defined in the extraction schema(see https://api.elis.rossum.ai/docs/#document-schema)
    :return: the datapoint matching the schema ID
    """
    for node in content:
        if node["schema_id"] == schema_id:
            return node
        elif "children" in node:
            # if the element with specified schema_id was found
            if found := find_by_schema_id(node["children"], schema_id):
                return found
    return None