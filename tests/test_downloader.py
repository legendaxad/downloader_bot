from services import VideoDownloader
downloader = VideoDownloader()

file_path = downloader.download(
    "https://youtube.com/shorts/hshrU_BlcJg?si=Pk9pkjQL3JvPGC5N"
)

print(file_path)