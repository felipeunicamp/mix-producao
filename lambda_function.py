import importlib
import mix_attempt1_celulose_lambda as _mod

def lambda_handler(event, context):
    importlib.reload(_mod)
    return {'statusCode': 200, 'body': 'dashboard_data.json atualizado no S3'}
