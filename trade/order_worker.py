import sys
import json
from misc.config import client

def place_order(params):
    try:
        order = client.ws_create_otoco_order(**params)
        return {"status": "success", "data": order}
    except Exception as e:
        return {"status": "error", "message": str(e)}



if __name__ == "__main__":
    # Receive parameters from stdin
    input_data = sys.stdin.read()
    params = json.loads(input_data)

    # Place the order and print the result
    result = place_order(params)
    print(json.dumps(result))



