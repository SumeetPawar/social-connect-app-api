import os
os.environ['CORS_ORIGINS'] = '["https://social-ui-exfnggf7cnffephw.eastus-01.azurewebsites.net","https://social-app-ui-qa.azurewebsites.net","https://cbiqa.dev.honeywellcloud.com"]'

from app.core.config import settings

print(f"Type: {type(settings.CORS_ORIGINS)}")
print(f"Count: {len(settings.CORS_ORIGINS)}")
print(f"URLs:")
for url in settings.CORS_ORIGINS:
    print(f"  - {url}")
