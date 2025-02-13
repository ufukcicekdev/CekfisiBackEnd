import boto3

AWS_ACCESS_KEY_ID = "DO00QY49VAUHRE4FMF4Y"
AWS_SECRET_ACCESS_KEY = "0U1OEdDC/EL2GgM5bgydlG00B68SR71RvSS4Rf1Kado"
AWS_STORAGE_BUCKET_NAME = "cekfisi"
AWS_S3_REGION_NAME = "fra1"  # DO Spaces'teki bölgen
AWS_S3_ENDPOINT_URL = f"https://{AWS_S3_REGION_NAME}.digitaloceanspaces.com"

s3_client = boto3.client(
    "s3",
    aws_access_key_id=AWS_ACCESS_KEY_ID,
    aws_secret_access_key=AWS_SECRET_ACCESS_KEY,
    endpoint_url=AWS_S3_ENDPOINT_URL
)

# Test: Bucket içeriğini listele
try:
    response = s3_client.list_objects_v2(Bucket=AWS_STORAGE_BUCKET_NAME)
    print(response)
except Exception as e:
    print(f"Hata oluştu: {e}")
