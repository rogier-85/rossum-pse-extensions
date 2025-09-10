import json
import urllib.request
from typing import List, Optional

configure_intent = {
    "intent": {
        "form": {
            "schema": {
                "type": "object",
                "properties": {
                    "custom_message": {
                        "type": "object",
                        "properties": {
                          "__fstring": {
                            "type": "string"
                          }
                        }
                    }
                }
            }
        }
    }
}


def rossum_hook_request_handler(payload: dict) -> dict:
    if payload["configure"]:
        return configure_intent
    else:
        send_slack_notification(payload)
        
        return {
            "messages": [
            {
                "type": "info",
                "content": "Slack message send",
                "id": "all",
            }
        ]
        }
        
        
    
    

def send_slack_notification(payload: dict) -> dict:

    # todo, convert to base url from payload
    annotation_url = "https://rogier-schippers.rossum.app/document/" + str(payload["annotation"]["id"])
    annotationdata = get_annotation_data(payload)

    #todo: fix empty value error for fields 
    vendorname = find_by_schema_id(annotationdata["content"], "sender_name")["content"]["value"]
    
    vendoraddress = find_by_schema_id(annotationdata["content"], "sender_address")["content"]["value"]
    vatid = find_by_schema_id(annotationdata["content"], "sender_vat_id")["content"]["value"]
    
    template = "[ { \"type\": \"header\", \"text\": { \"type\": \"plain_text\", \"text\": \"New vendor request\", \"emoji\": true } }, { \"type\": \"section\", \"fields\": [ { \"type\": \"mrkdwn\", \"text\": \"*Name:*\n{vendor_name}\" }, { \"type\": \"mrkdwn\", \"text\": \"*Address:*\n{vendor_address}\" } ] }, { \"type\": \"section\", \"fields\": [ { \"type\": \"mrkdwn\", \"text\": \"*VAT ID:*\n{vat_id}\" } ] }, { \"type\": \"actions\", \"elements\": [ { \"type\": \"button\", \"text\": { \"type\": \"plain_text\", \"emoji\": true, \"text\": \"Approve\" }, \"style\": \"primary\", \"value\": \"approve_{annotationid}\" }, { \"type\": \"button\", \"text\": { \"type\": \"plain_text\", \"emoji\": true, \"text\": \"Reject\" }, \"style\": \"danger\", \"value\": \"reject_{annotationid}\" } ] } ]"
    template = template.replace("{vendor_name}", vendorname)
    template = template.replace("{vendor_address}", vendoraddress)
    template = template.replace("{vat_id}", vatid)
    template = template.replace("{url}", annotation_url)
    template = template.replace("{annotationid}", str(payload["annotation"]["id"]))

    
    message = {
        'text': "New vendor notification",
        'blocks': template  
    }

    # send message to slack via webhook. fil correct URL
    url = "webhook_url"
    request = urllib.request.Request(url)
    request.method = "POST"
    
    urllib.request.urlopen(request, bytes(json.dumps(message), "UTF-8"))
    
    operations = []
    vendor_action_initiated = find_by_schema_id(annotationdata["content"], "vendor_action_initated")
    
    operation = create_replace_operation(vendor_action_initiated, "done")
    operations.append(operation)
    
    
    return json.dumps({"operations": operations})
    
  
def get_auth_token_from_payload(payload: dict) -> str:
    auth_token = payload.get("rossum_authorization_token")
    if not auth_token:
        raise HookConfigurationError(
            f"Authorization token not found in the payload. Configure Rossum API access at {payload['hook']}."
        )
    return auth_token


def get_annotation_data(payload: dict) -> dict:
    
    token = get_auth_token_from_payload(payload)
    url = payload["annotation"]["url"] + "/content"
    headers = {"Authorization": f"Bearer {token}"}
    
    print(token)
    print(url)
    print("test")
    
    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request) as response:
        print(response.status)
        data = json.loads(response.read())
    return data
    
def create_replace_operation(datapoint_id, new_value):
    return {
        "op": "replace",
        "id": datapoint_id["id"],
        "value": {"content": {"value": new_value}}
    }
    
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