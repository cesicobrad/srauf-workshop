"""A labelled technical sketch for testing uploads without personal photographs."""
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

root=Path(__file__).resolve().parents[1]
image=Image.new('RGB',(900,600),'#f6f4e9');draw=ImageDraw.Draw(image)
try:font=ImageFont.truetype('C:/Windows/Fonts/arial.ttf',27);small=ImageFont.truetype('C:/Windows/Fonts/arial.ttf',20)
except OSError:font=ImageFont.load_default(size=27);small=ImageFont.load_default(size=20)
draw.text((40,32),'EXAMPLE ATTACHMENT - FRAME SKETCH',font=font,fill='#174f43')
draw.text((40,75),'Fictional illustration for the upload demonstration',font=small,fill='#66746b')
draw.line([(125,460),(310,170),(565,460),(125,460),(395,225),(735,225)],fill='#174f43',width=18,joint='curve')
draw.line([(690,150),(750,150),(735,225),(820,460)],fill='#174f43',width=18,joint='curve')
draw.line([(280,170),(350,170)],fill='#174f43',width=18)
draw.line([(565,460),(520,295)],fill='#174f43',width=12)
draw.ellipse((525,420,605,500),outline='#174f43',width=9)
draw.line([(393,268),(423,272),(405,293),(439,303),(422,323)],fill='#c65d3b',width=10)
draw.ellipse((370,245,465,340),outline='#c65d3b',width=3)
draw.line([(465,290),(640,340)],fill='#c65d3b',width=3)
draw.text((590,350),'Area to inspect',font=small,fill='#a04d39')
draw.text((40,550),'Staff must assess actual photos before quoting a real repair.',font=small,fill='#66746b')
image.save(root/'web'/'sample-repair.png')
print('Created the labelled upload example.')
