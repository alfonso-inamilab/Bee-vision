######################################################################################
##      Align RGB & UV cameras using the RS depth as reference                      ##
##      uses depth value of each pixel to estimate the disparity on the other image ##
######################################################################################
import pyrealsense2 as rs
from numpy.linalg import inv
import numpy as np
import cv2 as cv

resX = 1280
resY = 720
baseline_Uv = 17 #mm (not final value yet, confirm with calibration data)
fx_Uv = 4.2353589064127340e+02 #fx from camera instrinsics (since it is vertical stereo)  
Tdiv = 1000.0
Rtmul = 1.3
clipping_distance_in_meters = 1.15 #1 meter
alphaValue = 1.0  #alpha for the mask transparency
baseLine = 26.0  #baseline redefined with T[1]
mapx1=None; mapx2=None; mapy1=None; mapy2=None

def erosion(src,eType,eSize):
    eSize = eSize
    eType = eType  # cv.MORPH_RECT, cv.MORPH_CROSS, cv.MORPH_ELLIPSE
    element = cv.getStructuringElement(eType, (2*eSize + 1, 2*eSize+1), (eSize, eSize))
    return cv.erode(src, element)

def dilatation(src,dType,dSize):
    dSize = dSize
    dType = dType  # cv.MORPH_RECT, cv.MORPH_CROSS, cv.MORPH_ELLIPSE
    element = cv.getStructuringElement(dType, (2*dSize + 1, 2*dSize+1), (dSize, dSize))
    return cv.dilate(src, element)

#This is a faster compositor
def fastAlphaBlend(fg,bg,alpha):
    '''
    Composit fg image onto bg image according to alpha. Alpha should be float [0.0, 1.0]
    uint8(fg * a + bg * (1-a))
    :param fg: Foreground image, should be same shape as bg
    :param bg: Background, should be same shape as fg
    :param alpha: Alpha mask image 32FC1 [0.0 1.0] image same widthxheight as foreground
    :return: 8U blended image
    '''
    #original version
    # a = (np.multiply(alpha, 1.0 / 255))[:,:,np.newaxis]
    # blended = cv.convertScaleAbs(fg * a + bg * (1-a))
    # MY VERSION
    a = alpha[:, :, np.newaxis]
    blended = cv.convertScaleAbs(fg * a + bg * (1-a))
    return blended

#calculates the rectify matrices from the camera parameters
def undistort(M1, M2, D1, D2, R1, R2, P1, P2):
    # PROBAR ESTA OPCION !!! OPCIONAL 1!!
    # https://docs.opencv.org/3.4/dc/dbb/tutorial_py_calibration.html
    # newcameramtx, roi = cv.getOptimalNewCameraMatrix(mtx, dist, (w,h), 1, (w,h))
    
    #Mat rmap[2][2];
    mapx1 = np.ndarray(shape=(resY, resX, 1), dtype='float32')
    mapy1 = np.ndarray(shape=(resY, resX, 1), dtype='float32')
    mapx2 = np.ndarray(shape=(resY, resX, 1), dtype='float32')
    mapy2 = np.ndarray(shape=(resY, resX, 1), dtype='float32')

    mapx1, mapy1 = cv.initUndistortRectifyMap(M1, D1, R1, P1,(resX, resY), cv.CV_16SC2)
    mapx2, mapy2 = cv.initUndistortRectifyMap(M2, D2, R2, P2,(resX, resY), cv.CV_16SC2)
    return  mapx1, mapx2, mapy1, mapy2

#Function to read the extrincics from the file
def loadCamFiles(fExt, fInt):
    #Load extrinsic matrix variables 
    fext = cv.FileStorage(fExt, cv.FILE_STORAGE_READ) 
    R = fext.getNode("R").mat()
    T = fext.getNode("T").mat()
    R1 = fext.getNode("R1").mat()
    R2 = fext.getNode("R2").mat()
    P1 = fext.getNode("P1").mat()
    P2 = fext.getNode("P2").mat()
    Q = fext.getNode("Q").mat()
    baseLine = T[1]

    #Load intrinsic matrix variables  (only for UV chinese camera)
    # M1 is the Real Sense !!! 
    fint = cv.FileStorage(fInt, cv.FILE_STORAGE_READ) 
    M1 = fint.getNode("M1").mat()  #cameraMatrix[0]
    D1 = fint.getNode("D1").mat()  #distCoeffs[0]
    M2 = fint.getNode("M2").mat()  #cameraMatrix[1]
    D2 = fint.getNode("D2").mat()  #distCoeffs[2]

    # print("R")
    # print(R)
    # print("T")
    # print(T)

    ######  Rt*1.3 and T/26  WHYYYYYY ????? !!!! 
    #format extrinsics for OpenCV use
    Rt =  np.zeros((4,4), dtype=np.float32) #rotation matrix
    Rt[0][0] = R[0][0]*Rtmul; Rt[0][1] = R[0][1]*Rtmul;  Rt[0][2] = R[0][2]*Rtmul; Rt[0][3] = T[0]/Tdiv
    Rt[1][0] = R[1][0]*Rtmul; Rt[1][1] = R[1][1]*Rtmul;  Rt[1][2] = R[1][2]*Rtmul; Rt[1][3] = T[1]/Tdiv
    Rt[2][0] = R[2][0]*Rtmul; Rt[2][1] = R[2][1]*Rtmul;  Rt[2][2] = R[2][2]*Rtmul; Rt[2][3] = T[2]/Tdiv
    Rt[3][0] = 0; Rt[3][1] = 0;  Rt[3][2] = 0; Rt[3][3] = 1  
    # print ("Rt to UV")
    # print (Rt)

    # print ("RS cam intrinsics")
    # print (M1)
    # print ("UV cam intrinsics")
    # print (M2)
    global mapx1; global mapx2; global mapy1; global mapy2;
    mapx1, mapx2, mapy1, mapy2 = undistort(M1, M2, D1, D2, R1, R2, P1, P2)

    fint.release()
    fext.release()
    return Rt, M2, D2

#rectifies the stereo camera pair
def remap(left, right):  #left is infra, right is normal 
    #remap(mg, rimg, rmap[k][0], rmap[k][1], INTER_LINEAR)
    recRight = np.full_like(right, 0, dtype="uint8")
    recLeft = np.full_like(left, 0, dtype="uint8")

    recLeft = cv.remap(left, mapx1, mapy1, cv.INTER_LINEAR)
    recRight = cv.remap(right, mapx2, mapy2, cv.INTER_LINEAR) 
    #recRight = cv.warpPerspective(recRight, self.R1, (self.width, self.height))

    return recLeft, recRight
    # return self.cutImage(recLeft, recRight)


cap = cv.VideoCapture(3)  #open chinese UV camera 3

# Configure depth and color streams
pipeline = rs.pipeline()
config = rs.config()
config.enable_stream(rs.stream.depth, resX, resY, rs.format.z16, 30)
config.enable_stream(rs.stream.color, resX, resY, rs.format.bgr8, 30)

# Start streaming
cfg = pipeline.start(config)

# RS colorizer object
colorizer = rs.colorizer()

# Getting the depth sensor's depth scale (see rs-align example for explanation)
depth_sensor = cfg.get_device().first_depth_sensor()
depth_scale = depth_sensor.get_depth_scale()
# print("Depth Scale is: " , depth_scale)

# Gets RealScene intrinsic parameters
i_profile = cfg.get_stream(rs.stream.depth) # Fetch stream profile for depth stream
intr = i_profile.as_video_stream_profile().get_intrinsics() # Downcast to video_stream_profile and fetch intrinsics
c_profile = cfg.get_stream(rs.stream.color) # Fetch stream profile for depth stream
c_intr = c_profile.as_video_stream_profile().get_intrinsics() # Downcast to video_stream_profile and fetch intrinsics

dev = cfg.get_device()
depth_sensor = dev.first_depth_sensor()
# depth_sensor.set_option(rs.option.emitter_enabled, True)  # INFRARED PROJECTOR TURN ON

if (depth_sensor.supports(rs.option.emitter_enabled)):
    depth_sensor.set_option(rs.option.emitter_enabled, False)  #INFRARED PROJECTOR TURN OFF

# Getting the depth sensor's depth scale (see rs-align example for explanation)
depth_scale = depth_sensor.get_depth_scale()
# print("Depth Scale is: " , depth_scale)

# We will be removing the background of objects more than
#  clipping_distance_in_meters meters away

clipping_distance = clipping_distance_in_meters / depth_scale

#Build RS depth intrinsics in OpenCV matrix format
# K = [fx 0 cx; 
#      0 fy cy; 
#      0  0  1]
depth_cam_matrix = np.zeros((3,3), dtype=np.float)
depth_cam_matrix[0][0] =  intr.fx #fx
depth_cam_matrix[0][2] =  intr.ppx #cx
depth_cam_matrix[1][1] =  intr.fy #fy
depth_cam_matrix[1][2] =  intr.ppy #cy
depth_cam_matrix[2][2] = 1
# print ("RS infrared intrinsics")
# print (depth_cam_matrix)

# scaled_depth = depth_cam_matrix.copy()
# scaled_depth[0][0] = intr.fx / 1.364740861736523
# scaled_depth[1][1] = intr.fy / 1.364740861736523
# print (scaled_depth)

color_cam_matrix = np.zeros((3,3), dtype=np.float)
color_cam_matrix[0][0] =  c_intr.fx #fx
color_cam_matrix[0][2] =  c_intr.ppx #cx
color_cam_matrix[1][1] =  c_intr.fy #fyq
color_cam_matrix[1][2] =  c_intr.ppy #cy
color_cam_matrix[2][2] =  1
color_distort = np.zeros(5, dtype=np.float)
color_distort[0] = c_intr.coeffs[0]  #k1
color_distort[1] = c_intr.coeffs[1]  #k2
color_distort[2] = c_intr.coeffs[2]  #p1
color_distort[3] = c_intr.coeffs[3]  #p2
color_distort[4] = c_intr.coeffs[4]  #k3

# RS extrinsics to OpenCV translation vector and rotation matrix 
# Get the extrinsics between the cameras from the calibration YML file 
Rt_to_Uv, uv_cam_matrix, uv_cam_distort = loadCamFiles('./rs_params/extrinsics.yml', './rs_params/intrinsics.yml')

# Gets RealSense camera
extrinsics = i_profile.get_extrinsics_to(c_profile)

Rt_to_Rgb = np.zeros((4,4), dtype=np.float32) #rotation matrix
Rt_to_Rgb[0][0] = extrinsics.rotation[0]; Rt_to_Rgb[0][1] = extrinsics.rotation[1];  Rt_to_Rgb[0][2] = extrinsics.rotation[2]; Rt_to_Rgb[0][3] = extrinsics.translation[0] 
Rt_to_Rgb[1][0] = extrinsics.rotation[3]; Rt_to_Rgb[1][1] = extrinsics.rotation[4];  Rt_to_Rgb[1][2] = extrinsics.rotation[5]; Rt_to_Rgb[1][3] = extrinsics.translation[1] 
Rt_to_Rgb[2][0] = extrinsics.rotation[6]; Rt_to_Rgb[2][1] = extrinsics.rotation[7];  Rt_to_Rgb[2][2] = extrinsics.rotation[8]; Rt_to_Rgb[2][3] = extrinsics.translation[2]
Rt_to_Rgb[3][0] = 0; Rt_to_Rgb[3][1] = 0;  Rt_to_Rgb[3][2] = 0; Rt_to_Rgb[3][3] = 1  
# print ("Rt to RGB")
# print (Rt_to_Rgb)

# Check if the UV camera opened successfully
if (cap.isOpened() == False):
    print("Error opening video stream or file")

#change webcam resolution
cap.set(cv.CAP_PROP_FRAME_WIDTH,resX)  
cap.set(cv.CAP_PROP_FRAME_HEIGHT,resY)  

tX = uv_cam_matrix[0][2] - uv_cam_matrix[0][2] * 1.3
tY = uv_cam_matrix[1][2] - uv_cam_matrix[1][2] * 1.3
T = np.float32([[1, 0, tX], [0, 1, tY]]) 
base = baseLine * depth_cam_matrix[1][1]  

# good base 13133.0205078125 px/mm 
# good T  27.93714518  mm
# good F  470.0917156422387   px ??
# real F  951.27001953125

try:
    while True:
        # Wait for a coherent pair of frames: depth and color
        frames = pipeline.wait_for_frames()
        depth_frame = frames.get_depth_frame()
        color_frame = frames.get_color_frame()
        ret, uv_image = cap.read()  #uv camera
        if not depth_frame or ret == False or not color_frame:
            continue

        depth_image = np.asanyarray(depth_frame.get_data())
        color_image = np.asanyarray(color_frame.get_data())
        
        # MAIN REGISTRATION FUNCTION    
        #  uv_rgb = K_rgb * [R | t] * z * inv(K_ir) * uv_ir 
        # unregisteredCameraMatrix	the camera matrix of the depth camera  (depth_cam_matrix)
        # registeredCameraMatrix	the camera matrix of the external camera (uv_cam_matrix)
        # registeredDistCoeffs	    the distortion coefficients of the external camera  (infra_cam_dist)
        # Rt	                    the rigid body transform between the cameras. Transforms points from depth camera frame to external camera frame. (Rt)
        # unregisteredDepth	        the input depth data  (depth_image)
        # outputImagePlaneSize      the image plane dimensions of the external camera (width, height)
        uv_regDepth = cv.rgbd.registerDepth( depth_cam_matrix, uv_cam_matrix, uv_cam_distort, Rt_to_Uv, depth_image, (resX, resY), depthDilation=False)
        # color_regDepth = cv.rgbd.registerDepth( depth_cam_matrix, color_cam_matrix, color_distort, Rt_to_Rgb, depth_image, (resX, resY), depthDilation=False)

        uv_regDepth = uv_regDepth.astype(float)*depth_scale #from UINT16 to FLOAT (in meters)
        
        #rescale the aligned depth map and the UV camera image 
        uv_image = cv.resize(uv_image, None, fx=1.3,fy=1.3, interpolation=cv.INTER_CUBIC)  #scale up UV depth map
        uv_image = cv.warpAffine(uv_image, T, (resX,resY))
        uv_resized = cv.resize(uv_regDepth, None, fx=1.3,fy=1.3, interpolation=cv.INTER_CUBIC)  #scale up UV depth map
        uv_resized = cv.warpAffine(uv_resized, T, (resX,resY))

        #EROTE AND DILATATE THE DEPTH MASK 
        mask = np.where((uv_resized > clipping_distance_in_meters) | (uv_resized <= 0.0), 0.0, alphaValue)  
        # mask = erosion(mask,cv.MORPH_RECT, 5)  # cv.MORPH_RECT, cv.MORPH_CROSS, cv.MORPH_ELLIPSE ksize 1~21 
        mask = dilatation(mask,cv.MORPH_RECT, 25)  # cv.MORPH_RECT, cv.MORPH_CROSS, cv.MORPH_ELLIPSE ksize 1~21 
        mask = cv.GaussianBlur( mask,(25 , 25), 0, 0 )

        #  FEATURE EXTRACTION BY CANNY 
        # Apply Canny to detect the borders
        color_image, uv_image = remap(color_image, uv_image)  #Rectify the images

        uvGray  = cv.cvtColor(uv_image, cv.COLOR_BGR2GRAY)
        rgbGray = cv.cvtColor(color_image, cv.COLOR_BGR2GRAY)

        # bilineal cauchy for border extraction
        uvGray = cv.bilateralFilter(uvGray, 7, 20, 20)   
        uvGray = cv.Canny(uvGray, 60, 150)    
        rgbGray = cv.bilateralFilter(rgbGray, 7, 20, 20)
        rgbGray = cv.Canny(rgbGray, 60, 150)    

        masko =  cv.convertScaleAbs(mask, alpha=alphaValue)  #mask to 8 bit
        uvGray = cv.bitwise_and(uvGray, uvGray, mask=masko)
        rgbGray = cv.bitwise_and(rgbGray, rgbGray, mask=masko)

        #feature extraction by Shi-Tomasi
        corners1 = cv.goodFeaturesToTrack(uvGray, maxCorners=10000, qualityLevel=0.01, minDistance=5, mask=masko ) #original qualityLevel=0.01 minDistance=10
        if (corners1 is not None):
            corners1 = np.int0(corners1)
            corners1 = np.squeeze(corners1)
            # corners2 = cv.goodFeaturesToTrack(rgbGray, maxCorners=10000, qualityLevel=0.01, minDistance=5, mask=masko )
            # corners2 = np.int0(corners2)
            # corners2 = np.squeeze(corners2)

            #get the disparity using the calibrated stereo formula
            if (len(corners1) > 0):
                corners2 = []
                for i in range(0, len (corners1), 1):
                    if corners1[i][0] < resX and  corners1[i][1] < resY:
                        Z =  uv_resized[corners1[i][1]] [corners1[i][0]] * 1000.0  #m to mm
                        if (Z > 0):
                            disp = base / Z  #in pixels (in theory )
                            corners2.append (  ( int(corners1[i][0]) , int(corners1[i][1] - disp) ) )

                # DEBUG draw the detected features (corners)
                for i in range(0, len (corners1), 1):
                    uv_image = cv.circle(uv_image,(corners1[i][0], corners1[i][1]) ,2,(255,0,255),-1)
                for i in range(0, len(corners2), 1):
                    color_image = cv.circle(color_image,(corners2[i][0], corners2[i][1]) ,2,(255,255,0),-1)
                
        #join the uv and the rgb images
        final = fastAlphaBlend(uv_image, color_image, mask)

        cv.imshow('rgb', color_image)
        cv.imshow('uv', uv_image)
        cv.imshow('Aligned UV/RGB', final)
        # cv.imshow('mask debug', mask)
        key = cv.waitKey(1)
        if key & 0xFF == ord('q'):
            break
        elif key == ord('a'):
            base = base - 100.0
            print ("alpha:"+ str(base))
        elif key == ord('d'):
            base = base + 100.0
            print ("alpha:"+ str(base))

finally:
    # Stop streaming
    pipeline.stop()



