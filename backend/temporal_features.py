import cv2
import numpy as np

class TemporalFeatureExtractor:
    SIZE=(160,96)
    CROP=0.90
    WINDOW_FRAMES=10
    FRAME_STEP=3

    @classmethod
    def preprocess_gray(cls, frame):
        h,w=frame.shape[:2]
        nw,nh=int(w*cls.CROP),int(h*cls.CROP)
        x=(w-nw)//2; y=(h-nh)//2
        frame=frame[y:y+nh,x:x+nw]
        frame=cv2.resize(frame,cls.SIZE)
        g=cv2.cvtColor(frame,cv2.COLOR_BGR2GRAY)
        g=cv2.GaussianBlur(g,(5,5),0)
        return g.astype(np.float32)/255.0

    @classmethod
    def extract_from_grays(cls, grays):
        diffs=[]; flow_stats=[]; hist_acc=np.zeros(8,np.float32)
        edge=[]; br=[]; sharp=[]
        for g in grays:
            br.append((g.mean(),g.std()))
            edge.append(cv2.Canny((g*255).astype(np.uint8),80,160).mean()/255.0)
            sharp.append(cv2.Laplacian((g*255).astype(np.uint8),cv2.CV_64F).var()/10000)
        for i in range(len(grays)-1):
            d=np.abs(grays[i+1]-grays[i])
            diffs += [d.mean(),d.std(),np.percentile(d,90),np.percentile(d,99)]
            flow=cv2.calcOpticalFlowFarneback(grays[i],grays[i+1],None,0.5,3,15,3,5,1.2,0)
            mag,ang=cv2.cartToPolar(flow[...,0],flow[...,1],angleInDegrees=False)
            flow_stats += [mag.mean(),mag.std(),np.percentile(mag,90),np.percentile(mag,99)]
            hist,_=np.histogram(ang,bins=8,range=(0,2*np.pi),weights=mag)
            s=hist.sum()
            if s>1e-6: hist=hist/s
            hist_acc += hist
        hist_acc /= max(1,len(grays)-1)
        return np.array(diffs+flow_stats+hist_acc.tolist()+[
            np.mean([x[0] for x in br]),np.mean([x[1] for x in br]),
            np.std([x[0] for x in br]),np.mean(edge),np.std(edge),np.max(edge),
            np.mean(sharp),np.max(sharp)
        ],np.float32)
