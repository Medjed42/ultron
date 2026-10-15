import os
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from .google_auth import get_google_credentials

def get_youtube_service():
    creds = get_google_credentials()
    return build('youtube', 'v3', credentials=creds)

def upload_video(file_path: str, title: str, description: str, category_id: str = "22"):
    """Uploads a video to YouTube."""
    try:
        if not os.path.exists(file_path):
            return {"status": "error", "message": "Video file not found."}
        
        youtube = get_youtube_service()
        
        body = {
            'snippet': {
                'title': title,
                'description': description,
                'categoryId': category_id
            },
            'status': {
                'privacyStatus': 'private' # private by default for safety
            }
        }
        
        insert_request = youtube.videos().insert(
            part=','.join(body.keys()),
            body=body,
            media_body=MediaFileUpload(file_path, chunksize=-1, resumable=True)
        )
        
        response = insert_request.execute()
        return {"status": "success", "video_id": response.get("id")}
    except Exception as e:
        return {"status": "error", "message": str(e)}
