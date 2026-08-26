from services import VideoService


video_service = VideoService()

metadata = video_service.get_metadata(
    "https://youtu.be/HWjCStB6k4o?si=sLvbHZHa4NzYDwBU"
)

print(f"Title: {metadata['title']}")
print(f"Duration: {metadata['duration']}")
print(f"Formats: {metadata['formats']}")