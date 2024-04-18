import boto3
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
async def backups(name, db=Depends(get_db)):
    container_backup_objects = []
    objects = []
    # try:
    if True:
        session = boto3.session.Session()
        s3_client = session.client(
            service_name='s3',
            endpoint_url=crud.get_setting(db, "backup_server_url").value,
            aws_access_key_id=crud.get_setting(db, "backup_server_access_key").value,
            aws_secret_access_key=crud.get_setting(db, "backup_server_secret_key").value,
        )
        all_backup_objects = \
            s3_client.list_objects(Bucket=crud.get_setting(db, "technical_name").value, Prefix=f"{name}/")[
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
    # except:
    #     pass

    return {"success": True, "backups": objects}
