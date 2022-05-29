import os
from pathlib import Path
import sys
from datetime import datetime
import time
import threading
from threading import Thread

import cv2
import numpy
import pytesseract
import logging

logging.basicConfig(
    level=logging.getLevelName("DEBUG"),
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(os.path.join(sys.path[0], "ocr.log")),
        logging.StreamHandler()
    ]
)

_LOGGER = logging.getLogger("ocr")

WINDOW_NAME = "Realtime OCR"

OCR_SLEEP = 0.5

def tesseract_location(root):
    """
    Sets the tesseract cmd root and exits is the root is not set correctly

    Tesseract needs a pointer to exec program included in the install.
    Example: User/Documents/tesseract/4.1.1/bin/tesseract
    See tesseract documentation for help.
    """
    try:
        pytesseract.pytesseract.tesseract_cmd = root
    except FileNotFoundError:
        print("Please double check the Tesseract file directory or ensure it's installed.")
        sys.exit(1)


class RateCounter:
    """
    Class for finding the iterations/second of a process

    `Attributes:`
        start_time: indicates when the time.perf_counter() began
        iterations: determines number of iterations in the process

    `Methods:`
        start(): Starts a time.perf_counter() and sets it in the self.start_time attribute
        increment(): Increases the self.iterations attribute
        rate(): Returns the iterations/seconds
    """

    def __init__(self):
        self.start_time = None
        self.iterations = 0

    def start(self):
        """
        Starts a time.perf_counter() and sets it in the self.start_time attribute

        :return: self
        """
        self.start_time = time.perf_counter()
        return self

    def increment(self):
        """
        Increases the self.iterations attribute
        """
        self.iterations += 1

    def rate(self):
        """
        Returns the iterations/seconds
        """
        elapsed_time = (time.perf_counter() - self.start_time)
        return self.iterations / elapsed_time

    def render(self, frame: numpy.ndarray, rate: float) -> numpy.ndarray:
        """
        Places text showing the iterations per second in the CV2 display loop.

        This is for demonstrating the effects of multi-threading.

        :param frame: CV2 display frame for text destination
        :param rate: Iterations per second rate to place on image

        :return: CV2 display frame with rate added
        """

        cv2.putText(frame, "{} Iterations/Second".format(int(rate)),
                    (10, 35), cv2.FONT_HERSHEY_DUPLEX, 1.0, (255, 255, 255))
        return frame

class VideoStream:
    """Class for grabbing frames from CV2 video capture. """

    def __init__(self, src=0):
        self.stream = cv2.VideoCapture(src)
        (self.grabbed, self.frame) = self.stream.read()
        self.stopped = False
        cv2.namedWindow(WINDOW_NAME)

    def start(self):
        """
        Creates a thread targeted at get(), which reads frames from CV2 VideoCapture

        :return: self
        """
        Thread(target=self.get, args=()).start()
        return self

    def get(self):
        """
        Continuously gets frames from CV2 VideoCapture and sets them as self.frame attribute
        """
        while not self.stopped:
            (self.grabbed, self.frame) = self.stream.read()

    def get_video_dimensions(self):
        """
        Gets the width and height of the video stream frames

        :return: height `int` and width `int` of VideoCapture
        """
        width = self.stream.get(cv2.CAP_PROP_FRAME_WIDTH)
        height = self.stream.get(cv2.CAP_PROP_FRAME_HEIGHT)
        return int(width), int(height)

    def stop_process(self):
        """
        Sets the self.stopped attribute as True and kills the VideoCapture stream read
        """
        self.stopped = True
    
    def capture_image(self, frame: numpy.ndarray = None, captures=0):
        """
        Capture a .jpg during CV2 video stream. Saves to a folder /images in working directory.

        :param frame: CV2 frame to save
        :param captures: (optional) Number of existing captures to append to filename

        :return: Updated number of captures. If capture param not used, returns 1 by default
        """
        if frame is None:
            frame = self.frame

        cwd_path = os.getcwd()
        Path(cwd_path + '/images').mkdir(parents=False, exist_ok=True)

        now = datetime.now()
        # Example: "OCR 2021-04-8 at 12:26:21-1.jpg"  ...Handles multiple captures taken in the same second
        name = "OCR " + now.strftime("%Y-%m-%d") + " at " + now.strftime("%H:%M:%S") + '-' + str(captures + 1) + '.jpg'
        path = 'images/' + name
        cv2.imwrite(path, frame)
        captures += 1
        print(name)
        return captures
    
    

class OcrRegionSelection:
    def __init__(self, video_stream: VideoStream) -> None:
        self.selection = None 
        self.drag_start = None 
        self.is_selecting = False
        self.stream = video_stream
    
    def start(self):
        #return self
        """
        Creates a thread targeted at get(), which reads frames from CV2 VideoCapture

        :return: self
        """
        Thread(target=self.register_mouse_callback, args=()).start()
        return self
    
    def register_mouse_callback(self):
        cv2.setMouseCallback(WINDOW_NAME, self.draw_rectangle)

    # Method to track mouse events 
    def draw_rectangle(self, event, x, y, flags, param): 
        x, y = numpy.int16([x, y]) 

        if event == cv2.EVENT_LBUTTONDOWN:
            # Start selection
            if not self.is_selecting:
                self.drag_start = (x, y) 
                self.is_selecting = True
            # Confirm selection
            else:
                x_start, y_start = self.drag_start
                self.selection = (x_start, y_start, x, y)
                self.is_selecting = False
                self.stream.capture_image(self.get_cropped_frame(self.stream.frame))

        if event == cv2.EVENT_MOUSEMOVE:
            if self.is_selecting and self.drag_start:
                x_start, y_start = self.drag_start 
                self.selection = (x_start, y_start, x, y) 
    
    def render(self, frame: numpy.ndarray) -> numpy.ndarray:
        if self.selection:
            cv2.rectangle(frame, (self.selection[0], self.selection[1]), (self.selection[2], self.selection[3]), (0, 255, 0), 2)
        else:
            (h, w) = frame.shape[:2]
            cv2.putText(frame, "Start drawing the OCR region", (100, h//2), cv2.FONT_HERSHEY_SIMPLEX, 1.5, (255, 255, 255))
            cv2.putText(frame, "Click, drag, and click another time to confirm", (100, h//2+50), cv2.FONT_HERSHEY_SIMPLEX, 1.5, (255, 255, 255))
        return frame
    
    def has_selection(self):
        return self.selection is not None

    def get_cropped_frame(self, frame: numpy.ndarray) -> numpy.ndarray:
        return frame[self.selection[1]:self.selection[3], self.selection[0]:self.selection[2]]


class OCR:
    """Class for creating a pytesseract OCR process in a dedicated thread"""

    def __init__(self, video_stream: VideoStream, region_selection: OcrRegionSelection):
        self.ocr_match: str = None
        self.stopped: bool = False
        self.region_selection = region_selection
        self.video_stream = video_stream
        self.file = None

    def start(self):
        """
        Creates a thread targeted at the ocr process
        :return: self
        """
        Thread(target=self.do_ocr, args=()).start()
        return self

    def do_ocr(self):
        """
        Creates a process where frames are continuously grabbed from the exchange and processed by pytesseract OCR.
        Output data from pytesseract is stored in the self.boxes attribute.
        """
        while not self.stopped:
            if self.video_stream is not None and self.region_selection.has_selection():
                try:
                    frame = self.video_stream.frame
                    frame = self.region_selection.get_cropped_frame(frame)

                    #Convert to grayscale for easier OCR detection
                    frame = cv2.cvtColor(frame, cv2.COLOR_RGB2GRAY)
                    # frame = cv2.adaptiveThreshold(frame, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 11, 2)
                    
                    match = pytesseract.image_to_string(
                        frame,
                        config="-c tessedit_char_whitelist='0123456789.'"
                        #config='outputbase nobatch digits'
                    )
                    _LOGGER.debug(f"OCR match: {match}")
                    if len(match) > 0:
                        self.ocr_match = match
                        self.write_result(self.ocr_match)
                    time.sleep(OCR_SLEEP)
                except:
                    _LOGGER.error(f"OCR error")
    
    def write_result(self, text: str):
        if self.file is None:
            self.file = open("ocr_results.txt", "a")
        
        text = text.strip()
        self.file.write(f"{time.time()};{text}\n")
        self.file.flush()

    def stop_process(self):
        """
        Sets the self.stopped attribute to True and kills the ocr() process
        """
        self.stopped = True
    
    def render(self, frame: numpy.ndarray) -> numpy.ndarray:
        #video_dimensions = self.video_stream.get_video_dimensions()
        frame = cv2.putText(frame, self.ocr_match, (100, 100), cv2.FONT_HERSHEY_DUPLEX, 1, (200, 200, 200))
        return frame


def ocr_stream(source: str = "0"):
    """
    Begins the video stream and text OCR in two threads, then shows the video in a CV2 frame with the OCR
    boxes overlaid in real-time.
    """

    video_stream = VideoStream(source).start()  # Starts reading the video stream in dedicated thread
    region_selection = OcrRegionSelection(video_stream).start()
    ocr = OCR(video_stream, region_selection).start()  # Starts optical character recognition in dedicated thread
    cps1 = RateCounter().start()

    print("OCR stream started")
    print("Active threads: {}".format(threading.activeCount()))

    # Main display loop
    print("\nPUSH q TO VIEW VIDEO STREAM\n")
    i = 0
    while True:
        i += 1
        # Quit condition:
        pressed_key = cv2.waitKey(1) & 0xFF
        if pressed_key == ord('q'):
            video_stream.stop_process()
            ocr.stop_process()
            print("OCR stream stopped\n")
            break

        frame = video_stream.frame  # Grabs the most recent frame read by the VideoStream class

        # # # All display frame additions go here # # # CUSTOMIZABLE
        frame = cps1.render(frame, cps1.rate())
        frame = ocr.render(frame)
        frame = region_selection.render(frame)
        # # # # # # # # # # # # # # # # # # # # # # # #

        if ocr.ocr_match is not None and i % 100 == 0:
            print(ocr.ocr_match)

        cv2.imshow(WINDOW_NAME, frame)
        cps1.increment()  # Incrementation for rate counter
