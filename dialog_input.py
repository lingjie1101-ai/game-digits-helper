"""Locate the confirmation input using OCR anchors and OpenCV components."""
from dataclasses import dataclass
import re
import unicodedata
import cv2
import numpy as np


@dataclass
class DialogInput:
    region: tuple
    anchor_box: tuple
    anchor_pixels: np.ndarray
    size: tuple
    digits: str
    code_box: tuple
    code_ink: np.ndarray
    buttons: tuple
    prompt_ink: np.ndarray

    def layout_matches(self,image):
        if image is None or image.size!=self.size:return False
        x,y,w,h=self.region
        area=image.crop((round(x*image.width),round(y*image.height),round((x+w)*image.width),round((y+h)*image.height)))
        if light_mask(area).mean()<.70:return False
        for box in self.buttons:
            rgb=np.asarray(image.crop(box)).astype('int16')
            if rgb.size==0 or ((rgb[:,:,0]>rgb[:,:,1]*1.4)&(rgb[:,:,0]>rgb[:,:,2]*1.2)&(rgb[:,:,0]>70)).mean()<.25:return False
        return True

    def ocr_box(self):
        x,y,w,h=self.region;W,H=self.size
        return (max(0,round(x*W)-12),max(0,self.anchor_box[1]-12),min(W,round((x+w)*W)+12),min(H,max(b[3] for b in self.buttons)+12))

    def matches(self, image):
        if not self.layout_matches(image):return False
        if not np.array_equal(yellow_ink(image.crop(self.code_box)),self.code_ink):return False
        current=text_ink(image.crop(self.anchor_box))
        union=np.count_nonzero(current|self.prompt_ink)
        return bool(union and np.count_nonzero(current&self.prompt_ink)/union>=.96)


def yellow_ink(image):
    hsv=cv2.cvtColor(np.asarray(image.convert('RGB')),cv2.COLOR_RGB2HSV)
    return (hsv[:,:,0]>=18)&(hsv[:,:,0]<=42)&(hsv[:,:,1]>=140)&(hsv[:,:,2]>=150)


def text_ink(image):
    rgb=np.asarray(image.convert('RGB'))
    return (rgb.min(axis=2)>=150)|yellow_ink(image)


def light_mask(image):
    rgb=np.asarray(image.convert('RGB'));low=rgb.min(axis=2);high=rgb.max(axis=2)
    return (low>=110)&(high<=240)&((high.astype('int16')-low)<65)


def candidate_at(image,x,y):
    """A light long field may request OCR, never permission to send input."""
    if image is None or not (0<=x<image.width and 0<=y<image.height):return False
    left=max(0,round(x-image.width*.4));right=min(image.width,round(x+image.width*.8))
    top=max(0,round(y-30));bottom=min(image.height,round(y+30))
    mask=light_mask(image.crop((left,top,right,bottom))).astype('uint8')*255
    mask=cv2.morphologyEx(mask,cv2.MORPH_OPEN,np.ones((3,3),'uint8'))
    mask=cv2.morphologyEx(mask,cv2.MORPH_CLOSE,np.ones((3,9),'uint8'))
    count,labels,stats,centroids=cv2.connectedComponentsWithStats(mask,connectivity=8)
    return any(w>=image.width*.30 and 7<=h<=32 and w/h>10 and area/(w*h)>.75 and bx+left<=x<bx+left+w and by+top<=y<by+top+h for bx,by,w,h,area in stats[1:])


def bounds(block):
    points = block.get('box', [])
    if len(points) != 4:
        return None
    return (min(p[0] for p in points), min(p[1] for p in points),
            max(p[0] for p in points), max(p[1] for p in points))


def locate_input(image, blocks, digits, keywords):
    """Return one proven light input beneath the recognized prompt; never guess."""
    if not digits:
        return None, '等待确认弹窗'
    pattern = re.compile('(?:'+'|'.join(re.escape(w) for w in keywords)+r')[\s:：,，]*'+re.escape(digits)+r'(?![0-9A-Za-z])')
    anchors=[];buttons=[]
    for block in blocks:
        text=unicodedata.normalize('NFKC',str(block['text']))
        box=bounds(block)
        if box is None:continue
        if pattern.search(text):anchors.append(box)
        if re.sub(r'\s+','',text) in ('确定','确认','取消'):buttons.append((text,box))
    if len(anchors)!=1:
        return None, '确认文字位置不明确，未启用点击粘贴'
    ax,ay,ar,ab=anchors[0];line_height=max(6,ab-ay);line_width=ar-ax
    # Search only directly below this prompt, never the rest of the game's UI.
    pairs=[]
    for text,c in buttons:
        if re.sub(r'\s+','',text) not in ('确定','确认'):continue
        for other,d in buttons:
            if re.sub(r'\s+','',other)!='取消':continue
            if c[2]<d[0] and abs(c[1]-d[1])<line_height and ab+line_height<=c[1]<=ab+line_height*6 and c[0]>=ax:
                pairs.append((c,d))
    if len(pairs)!=1:return None,'确认弹窗按钮位置不明确，未启用点击粘贴'
    confirm_box,cancel_box=pairs[0]
    left=max(0,int(ax-2));right=min(image.width,int(max(ar,cancel_box[2])+line_height))
    top=max(0,int(ab+1));bottom=min(image.height,int(min(confirm_box[1],cancel_box[1])-2))
    if right<=left or bottom<=top:return None,'确认文字下方没有可用区域'
    rgb=np.asarray(image.crop((left,top,right,bottom)))
    low=rgb.min(axis=2);high=rgb.max(axis=2)
    mask=((low>=110)&(high<=240)&((high.astype('int16')-low)<65)).astype('uint8')*255
    mask=cv2.morphologyEx(mask,cv2.MORPH_OPEN,cv2.getStructuringElement(cv2.MORPH_RECT,(3,3)))
    kernel=cv2.getStructuringElement(cv2.MORPH_RECT,(max(3,round(line_height*3)),3))
    pad=kernel.shape[1]
    padded=cv2.copyMakeBorder(mask,pad,pad,pad,pad,cv2.BORDER_CONSTANT,value=0)
    mask=cv2.morphologyEx(padded,cv2.MORPH_CLOSE,kernel)[pad:-pad,pad:-pad]
    count,labels,stats,centroids=cv2.connectedComponentsWithStats(mask,connectivity=8)
    candidates=[]
    for x,y,w,h,area in stats[1:]:
        x,y,w,h,area=map(int,(x,y,w,h,area));x+=left;y+=top
        if not max(100,line_width*.65)<=w<=line_width*1.7:continue
        if not 6<=h<=line_height*2.5 or w/h<12 or area/(w*h)<.82:continue
        if not 0<=y-ab<=line_height*3.3:continue
        # Require the two action labels below this same input, in the same row.
        ok=[(text,b) for text,b in buttons if x<=b[0]<=b[2]<=x+w+line_height and y+h<=b[1]<=y+h+line_height*4]
        confirm=[b for t,b in ok if re.sub(r'\s+','',t) in ('确定','确认')]
        cancel=[b for t,b in ok if re.sub(r'\s+','',t)=='取消']
        if not any(c[2]<d[0] and abs(c[1]-d[1])<line_height for c in confirm for d in cancel):continue
        candidates.append((x,y,w,h))
    if len(candidates)!=1:
        return None, '未唯一定位确认输入框，未发送按键'
    x,y,w,h=candidates[0]
    # Use the detected input fill; action buttons stay outside the hit area.
    inset=0
    region=((x+inset)/image.width,(y+inset)/image.height,(w-2*inset)/image.width,(h-2*inset)/image.height)
    anchor=(max(0,int(ax)),max(0,int(ay)),min(image.width,int(ar+1)),min(image.height,int(ab+1)))
    yellow=yellow_ink(image.crop(anchor));columns=np.flatnonzero(yellow.any(axis=0))
    if not len(columns):return None,'确认数字的颜色区域不明确，未启用点击粘贴'
    groups=np.split(columns,np.flatnonzero(np.diff(columns)>max(3,round(line_height*.4)))+1)
    first=groups[0];cx0,cx1=int(first[0]),int(first[-1])+1
    if not line_height*len(digits)*.20<=cx1-cx0<=line_height*len(digits)*1.2:return None,'确认数字图像范围不明确，未启用点击粘贴'
    code_box=(anchor[0]+cx0,anchor[1],anchor[0]+cx1,anchor[3])
    button_boxes=tuple((max(0,int(b[0])-3),max(0,int(b[1])-3),min(image.width,int(b[2])+4),min(image.height,int(b[3])+4)) for b in (confirm_box,cancel_box))
    target=DialogInput(region,anchor,np.asarray(image.crop(anchor)).copy(),image.size,digits,code_box,yellow_ink(image.crop(code_box)),button_boxes,text_ink(image.crop(anchor)))
    return target,'已定位：直接左键点击游戏里的确认输入框'
