import boto3
import docker
from fastapi import APIRouter, Depends
import crud
from api.helper import service_logs
from core.db import get_db

router = APIRouter()


@router.get("/{name}/logs/")
async def logs(name, db=Depends(get_db)):
    container_logs = service_logs(name)
    return {"success": True, "logs": container_logs}


@router.get("/{name}/usages/")
async def logs(name, db=Depends(get_db)):
    container_usages = crud.get_single_service_usages(db, name)
    return {"success": True, "usages": container_usages}


@router.get("/{name}/backups/")
async def backups(name: str, db=Depends(get_db)):
    container_backup_objects = []
    objects = []
    try:
        session = boto3.session.Session()
        s3_client = session.client(
            service_name='s3',
            endpoint_url=crud.get_setting(db, "backup_server_url").value,
            aws_access_key_id=crud.get_setting(db, "backup_server_access_key").value,
            aws_secret_access_key=crud.get_setting(db, "backup_server_secret_key").value,
        )
        bucket = crud.get_setting(db, "technical_name").value

        try:
            bucket = crud.get_setting(db, "backup_server_bucket").value
        except:
            pass

        all_backup_objects = \
            s3_client.list_objects(Bucket=bucket, Prefix=f"{name}/")[
                'Contents']
        for ob in all_backup_objects:
            if ob['Key'].startswith(f"{name}/"):
                container_backup_objects.append(ob)

        for container_backup_object in container_backup_objects:
            if not container_backup_object['Key'].endswith("/"):
                objects.append({
                    "object": container_backup_object['Key'],
                    "size": round(container_backup_object['Size'] / 1024, 1),
                    "status": "active",
                    "updated": container_backup_object['LastModified'],
                    "created": container_backup_object['LastModified'],
                })
    except:
        pass

    return {"success": True, "backups": objects}


@router.post("/{name}/backups/get/")
async def get_backups(name: str, object_name: str, db=Depends(get_db)):
    session = boto3.session.Session()
    s3_client = session.client(
        service_name='s3',
        endpoint_url=crud.get_setting(db, "backup_server_url").value,
        aws_access_key_id=crud.get_setting(db, "backup_server_access_key").value,
        aws_secret_access_key=crud.get_setting(db, "backup_server_secret_key").value,
    )
    bucket = crud.get_setting(db, "technical_name").value

    try:
        bucket = crud.get_setting(db, "backup_server_bucket").value
    except:
        pass

    url = s3_client.generate_presigned_url('get_object',
                                           Params={'Bucket': bucket,
                                                   'Key': object_name},
                                           ExpiresIn=36000)

    return {"success": True, "url": url}
