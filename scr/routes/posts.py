# scr\routes\posts.py
from fastapi import (
    APIRouter,
    HTTPException,
    Path,
    UploadFile,
    Depends,
    File,
    Form,
    Response,
    Query,
)
from fastapi.responses import JSONResponse
from pydantic import ValidationError
import cloudinary.uploader
import cloudinary.api
import os
import shutil
import uuid
from typing import Any, List, Optional
import logging
from datetime import datetime

from sqlalchemy.orm import Session
from starlette.requests import Request
from starlette import status
from starlette.status import HTTP_404_NOT_FOUND
from cloudinary.uploader import upload, destroy

from scr.database.models import Role, User, Image, Comment
from scr.schemas import PostCreate, PostList, PostSingle, UserResponse, UserDb, TagModel
from scr.database.db import get_db
from scr.services.auth import auth_service
from scr.services.posts import PostServices
from scr.services.tags import TagServices, Tag
from scr.services.roles import RoleAccess
from scr.conf.config import config
# from src.services.cloudinary_srv import CloudinaryService
from scr.services.roles import RoleAccess

allowed_operation_admin = RoleAccess([Role.admin])
allowed_operation_search_by_user = RoleAccess([Role.admin, Role.moderator])


class Cloudinary:
    cloudinary.config(
        cloud_name=config.CLD_NAME,
        api_key=config.CLD_API_KEY,
        api_secret=config.CLD_API_SECRET,
        secure=True,
    )


posts_router = APIRouter(prefix="", tags=["Posts of picture"])
tags_router = APIRouter(prefix="", tags=["Tags of picture"])

tag_services = TagServices(Tag)
post_services = PostServices(Image)
# comment_services = CommentServices(Comment)

allowed_operation_create = RoleAccess([Role.admin, Role.moderator, Role.user])
allowed_operation_read = RoleAccess([Role.admin, Role.moderator, Role.user])
allowed_operation_update = RoleAccess([Role.admin, Role.moderator, Role.user])
allowed_operation_delete = RoleAccess([Role.admin, Role.moderator, Role.user])


# публікуємо світлину
@posts_router.post("/publication", 
                   response_model=PostSingle, 
                   response_model_exclude_unset=True, 
                   dependencies=[Depends(allowed_operation_create)])
async def upload_images_user(
    file: UploadFile = File(),
    text: str = Form(...),
    tags: List[str] = Form([]),
    current_user: UserDb = Depends(auth_service.get_current_user),
    db: Session = Depends(get_db),
):
    """
    The upload_images_user function uploads an image to the Cloudinary cloud storage service.
    The function also saves the image's metadata in a PostgreSQL database.
    
    
    :param file: UploadFile: Receive the file from the user
    :param text: str: Get the description of the image
    :param tags: List[str]: Get a list of tags from the form
    :param current_user: UserDb: Get the current user
    :param db: Session: Access the database
    :param : Get the current user
    :return: The following data:
    :doc-author: Trelent
    """
    try:
        img_content = await file.read()
        public_id = f"image_{current_user.id}_{uuid.uuid4()}"

        # Завантаження на Cloudinary
        response = cloudinary.uploader.upload(
            img_content, public_id=public_id, overwrite=True, folder="publication"
        )

        # Зберігання в базі даних
        image = Image(
            owner_id=current_user.id,
            url_original=response["secure_url"],
            description=text,
            url_original_qr="",
            updated_at=datetime.now(),
        )

        # Розділення тегів та перевірка кількості
        for tags_str in tags:
            tag_list = tags_str.split(",")
            tag_count = len(tag_list)
            print(f"Кількість тегів: {tag_count}")

            if tag_count > 5:
                raise HTTPException(
                    status_code=400, detail="Максимальна кількість тегів - 5"
                )

            for tag_name in tag_list:
                tag_name = tag_name.strip()

                # Чи існує тег з таким іменем
                tag = db.query(Tag).filter_by(name=tag_name).first()
                if tag is None:
                    # Якщо тег не існує, створюємо та зберігаємо
                    tag = Tag(name=tag_name)
                    db.add(tag)
                    db.commit()
                    db.refresh(tag)

                # Перевірка, чи тег вже приєднаний до світлини
                if tag not in image.tags:
                    image.tags.append(tag)

        db.add(image)
        db.commit()

        # інформація про світлину
        item = await post_services.get_p(db=db, id=image.id)

        if not item:
            raise HTTPException(
                status_code=HTTP_404_NOT_FOUND, detail="Запис не знайдений"
            )

        post_data = {
            "id": item.id,
            "owner_id": item.owner_id,
            "url_original": item.url_original,
            "tags": [tag.name for tag in item.tags],
            "description": item.description,
            "pub_date": item.created_at,
            "img": item.url_original,
            "text": "",
            "user": "",
        }

        return PostSingle(**post_data)
    except HTTPException as e:
        logging.error(f"Помилка валідації форми: {e}")
        raise
    except Exception as e:
        logging.error(f"Помилка завантаження зображення: {e}")
        raise


# отримаємо списк світлин по ідентифікації користувача
@posts_router.get('/user/{user_id}', 
                  response_model=list[PostList],
                  dependencies=[Depends(allowed_operation_read)])
async def post_list_by_user(
    user_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(auth_service.get_current_user),
    limit: int = Query(default=10, description="Кількість елементів на сторінці", ge=1),
    offset: int = Query(default=0, description="Зміщення сторінки", ge=0),
):
    """
    The post_list_by_user function returns a list of posts by the user with the given id.
    
    :param user_id: int: Specify the user id of the posts we want to retrieve
    :param db: Session: Pass the database session to the function
    :param user: User: Get the current user
    :param limit: int: Set the number of items per page
    :param description: Describe the parameter in the api documentation
    :param ge: Specify the minimum value of a parameter
    :param offset: int: Specify the offset of the page
    :param description: Describe the parameter in the documentation
    :param ge: Specify the minimum value for a parameter
    :param : Get the current user
    :return: A list of posts by the user
    :doc-author: Trelent
    """
    posts = post_services.get_post_list_by_user_paginated(db=db, user_id=user_id, limit=limit, offset=offset)
    if not posts:
        raise HTTPException(status_code=HTTP_404_NOT_FOUND, detail="Записи не знайдені")
    return JSONResponse(content=[post.json() for post in posts])


# отримувати світлину за унікальним посиланням в БД
@posts_router.get('/{id}', 
                  response_model=PostSingle, 
                  dependencies=[Depends(allowed_operation_read)])
async def get_post(id: int, 
                   db: Session = Depends(get_db),
                   user: User = Depends(auth_service.get_current_user)) -> Any:
    """
    The get_post function returns a single post by id.
    
    :param id: int: Specify the type of parameter
    :param db: Session: Pass the database session to the function
    :param user: User: Get the current user
    :return: A postsingle object
    :doc-author: Trelent
    """
    item = await post_services.get_p(db=db, id=id)

    if not item:
        raise HTTPException(status_code=HTTP_404_NOT_FOUND, detail="Запис не знайдений")

    post_data = {
        "id": item.id,
        "owner_id": item.owner_id,
        "url_original": item.url_original,
        "tags": [tag.name for tag in item.tags],
        "description": item.description,
        "pub_date": item.created_at,
        "img": item.url_original,
        "text": "",
        "user": "",
    }

    return PostSingle(**post_data)


# отримувати світлину за унікальним посиланням в cloudinary
# по аналогії пошуку за параметром в БД, але відповідь "detail": "Not Found"
# @posts_router.get('/post-url/{url_original}', response_model=PostSingle)
# async def get_post_by_url(url_original: str,
#                            db: Session = Depends(get_db),
#                            user: User = Depends(auth_service.get_current_user)):

#     try:
#         item = await post_services.get_post_url(db=db, url_original=url_original)

#         print("Item from the database:", item)  # Додайте це логування

#         if not item:
#             raise HTTPException(status_code=HTTP_404_NOT_FOUND, detail='Запис не знайдено')

#         post_data = {
#             'id': item.id,
#             'owner_id': item.owner_id,
#             'url_original': item.url_original,
#             'tags': [],
#             'description': item.description,
#             'pub_date': item.created_at.isoformat(),
#             'img': item.url_original,
#             'text': '',
#             'user': {
#                 'id': user.id,
#                 'username': user.username,
#                 'created_at': user.created_at.isoformat(),
#             },
#         }

#         return JSONResponse(content=post_data)

#     except Exception as e:
#         raise


# отримувати світлину за параметром в БД - працює та повертає значення
@posts_router.get('/descriptions/{description}', 
                  response_model=PostSingle, 
                  dependencies=[Depends(allowed_operation_read)])
async def get_post_by_description(description: str,
                                   db: Session = Depends(get_db),
                                   user: User = Depends(auth_service.get_current_user)):
    """
    The get_post_by_description function returns a post by description.
        Args:
            description (str): The post's description.
            db (Session, optional): SQLAlchemy Session. Defaults to Depends(get_db).
            user (User, optional): User object from auth_service.get_current_user(). Defaults to Depends(auth_service.get_current_user).

    :param description: str: Pass the description of the post to be retrieved
    :param db: Session: Pass the database session to the function
    :param user: User: Get the current user
    :return: A jsonresponse object
    :doc-author: Trelent
    """

    try:
        item = await post_services.get_post_by_description(
            db=db, description=description
        )

        if not item:
            raise HTTPException(
                status_code=HTTP_404_NOT_FOUND, detail="Запис не знайдено"
            )

        post_data = {
            "id": item.id,
            "owner_id": item.owner_id,
            "url_original": item.url_original,
            "tags": [tag.name for tag in item.tags],
            "description": item.description,
            "pub_date": item.created_at.isoformat(),
            "img": item.url_original,
            "text": "",
            "user": {
                "id": user.id,
                "username": user.username,
                "created_at": user.created_at.isoformat(),
            },
        }

        return JSONResponse(content=post_data)

    except Exception as e:
        raise 



# редагування опису світлини 
@posts_router.put('/{id}', 
                  response_model=PostSingle,
                  description="Редагування за id. Для власних даних. Administrator може будь які.", 
                  dependencies=[Depends(allowed_operation_update)])

async def update_image_description(
    id: int, 
    description: str, 
    db: Session = Depends(get_db),
    user: User = Depends(auth_service.get_current_user),) -> Any:
    """
    The update_image_description function updates the description of an image.
        Args:
            id (int): The ID of the image to update.
            description (str): The new description for the image.
    
    :param id: int: Specify the id of the image that we want to update
    :param description: str: Pass the new description to the function
    :param db: Session: Get the database connection
    :param user: User: Get the current user
    :return: A postsingle object
    :doc-author: Trelent
    """
    
    if user.role in allowed_operation_admin.allowed_roles:
        item = await post_services.get_p(db=db, id=id)
    else:
        item = await post_services.get_p_owner_id(db=db, id=id, owner_id=user.id)

    if not item:
        raise HTTPException(status_code=HTTP_404_NOT_FOUND, detail="Запис не знайдений")

    item.description = description
    item.updated_at = datetime.now()
    db.commit()

    updated_post_data = {
        "id": item.id,
        "owner_id": item.owner_id,
        "url_original": item.url_original,
        "tags": [tag.name for tag in item.tags],
        "description": item.description,
        "pub_date": item.created_at,
        "img": item.url_original,
        "text": "",
        "user": "",
    }

    return PostSingle(**updated_post_data)


# видалення світлини
@posts_router.delete('/{id}', 
                     response_model=dict, 
                     description="Видалення за id. Для власних даних. Administrator може видалити будь які.",
                     dependencies=[Depends(allowed_operation_delete)])

async def delete_image(
    id: int,
    db: Session = Depends(get_db),
    user: User = Depends(auth_service.get_current_user),
) -> dict:
    
    """
    The delete_image function deletes an image from the database and Cloudinary.
    
    :param id: int: Specify the id of the image to be deleted
    :param db: Session: Access the database
    :param user: User: Get the current user from the database
    :return: A dictionary with a message
    :doc-author: Trelent
    """

    if user.role in allowed_operation_admin.allowed_roles:
        item = await post_services.get_p(db=db, id=id)
    else:
        item = await post_services.get_p_owner_id(db=db, id=id, owner_id=user.id)

    if not item:
        raise HTTPException(status_code=HTTP_404_NOT_FOUND, detail="Запис не знайдений")

    try:
        destroy_result = destroy(public_id=item.public_id)
        print("Cloudinary Destroy Result:", destroy_result)
    except Exception as e:
        print("Error during Cloudinary destroy:", str(e))

    db.delete(item)
    db.commit()

    return {"message": "Запис видалено успішно"}


@posts_router.get("/tags/")
async def get_tags(
    db: Session = Depends(get_db),
    limit: int = Query(
        default=50, description="Кількість елементів на сторінці", ge=1, le=200
    ),
    offset: int = Query(default=0, description="Зміщення сторінки", ge=0),
):
    tags = post_services.get_tags_paginated(db=db, limit=limit, offset=offset)
    if tags:
        return tags
    raise HTTPException(status_code=HTTP_404_NOT_FOUND, detail="Записи не знайдені")


# отримувати світлину за параметрами в БД - працює та повертає значення
@posts_router.get("/search/", response_model=list[PostList])
async def search_post(
    description: str
    | None = Query(
        default=None, description="Пошук частковим за входженням тексту в опис"
    ),
    tag: str | None = Query(default=None, description="Пошук за тегом"),
    sort: str
    | None = Query(default=None, description="Сортування за датою створення: +, -"),
    db: Session = Depends(get_db),
    limit: int = Query(
        default=10, description="Кількість елементів на сторінці", ge=1, le=100
    ),
    offset: int = Query(default=0, description="Зміщення сторінки", ge=0),
):
    if description or tag:
        posts = post_services.search_posts_paginated(
            db=db,
            description=description,
            tag=tag,
            sort=sort,
            limit=limit,
            offset=offset,
        )
        if posts:
            return JSONResponse(content=[post.json() for post in posts])
    raise HTTPException(status_code=HTTP_404_NOT_FOUND, detail="Записи не знайдені")


# отримувати світлину за параметром в БД за користувачем
@posts_router.get(
    "/search/{user_id}",
    response_model=list[PostList],
    dependencies=[
        Depends(auth_service.get_current_user),
        Depends(allowed_operation_search_by_user),
    ],
    description="Admins, Moderators only",
)
async def search_post_by_user_id(
    description: str
    | None = Query(
        default=None, description="Пошук частковим за входженням тексту в опис"
    ),
    user_id: int = Path(description="Пошук за користувача ID"),
    tag: str | None = Query(default=None, description="Пошук за тегом"),
    sort: str
    | None = Query(default=None, description="Сортування за датою створення: +, -"),
    db: Session = Depends(get_db),
    limit: int = Query(
        default=10, description="Кількість елементів на сторінці", ge=1, le=100
    ),
    offset: int = Query(default=0, description="Зміщення сторінки", ge=0),
):
    if user_id or description or tag:
        posts = post_services.search_posts_paginated(
            db=db,
            description=description,
            tag=tag,
            user_id=user_id,
            sort=sort,
            limit=limit,
            offset=offset,
        )
        if posts:
            return JSONResponse(content=[post.json() for post in posts])
    raise HTTPException(status_code=HTTP_404_NOT_FOUND, detail="Записи не знайдені")
