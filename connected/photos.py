"""Validate uploads and store an image-only copy with orientation fixed and metadata removed."""
import base64
import binascii
import io
import warnings
from pathlib import PurePath

from PIL import Image, ImageOps

MAX_UPLOAD=4*1024*1024


def normalize_photo(filename,data_base64):
    try:
        raw=base64.b64decode(data_base64,validate=True)
    except (binascii.Error,ValueError):raise ValueError("The image upload was not valid base64.")
    if len(raw)>MAX_UPLOAD:raise ValueError("Each photo must be at most 4 MB.")
    if not raw:raise ValueError("That image is empty.")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error",Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(raw)) as image:
                if image.format not in ("JPEG","PNG","WEBP"):raise ValueError("Use a JPEG, PNG or WebP photo. SVG and animated files are not accepted.")
                if getattr(image,"is_animated",False):raise ValueError("Use a single image, not an animation.")
                if image.width*image.height>20000000:raise ValueError("Use an image smaller than 20 megapixels.")
                image.load()
                image=ImageOps.exif_transpose(image)
                image.thumbnail((1800,1800))
                if image.mode in ("RGBA","LA") or image.info.get("transparency") is not None:
                    rgba=image.convert("RGBA");clean=Image.new("RGB",rgba.size,"white");clean.paste(rgba,mask=rgba.getchannel("A"))
                else:clean=image.convert("RGB")
                out=io.BytesIO();clean.save(out,format="JPEG",quality=88)
                name=PurePath(filename.replace("\\","/")).name
                name="".join(c for c in name if c.isprintable())[:90] or "repair-photo"
                name=name.rsplit(".",1)[0]+".jpg"
                return {"filename":name,"data":out.getvalue(),"media_type":"image/jpeg","width":clean.width,"height":clean.height}
    except (Image.DecompressionBombError,Image.DecompressionBombWarning):raise ValueError("That image is too large to process safely.")
    except (OSError,SyntaxError):raise ValueError("That file could not be read as an image.")
