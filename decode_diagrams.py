import sys
sys.path.append('text/')
from readText import Options, create_text_read_envir, single_img, batch_of_images

import torch

import cv2
import numpy as np
from PIL import Image

YOLO_CLASSES = ['circle', 'rectangle', 'parallelogram', 'diamond', 'arrow', 'text']

device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')   

COLORS = [[ 81.92117536, 232.97617038, 143.66136337], # circle
          [190.0, 50.0,  50.0   ],                    # rectangle
          [100.0, 150.0, 250.0],                      # parallelogram
          [20.0, 100.0,  100.0],                      # diamond
          [200.0, 200.0, 200.0],                      # arrow
          [50.0, 50.0,  190.0]]                       # text
COLORS = np.asarray(COLORS)

def create_yolo_model(yoloModelPath):
    """
    Function that takes a path to a trained yolov5s model and returns the model file needed.
    """
    print(f"Creating yolo model from {yoloModelPath}")
    yolo_model = torch.hub.load("yolov5", "custom", yoloModelPath, source="local").to(device)
    return yolo_model

def create_text_model(text_model_path):
    """
    Function that takes a path to a trained TRBA text reading model and returns the attention label converter, model file, and collater needed to decode its detections.
    """
    print(f"Creating text model from {text_model_path}")
    opt = Options(text_model_path)
    converter, text_model, AlignCollate_demo = create_text_read_envir(opt)
    return converter, text_model, AlignCollate_demo

def _determine_text_to_be_read(p_classes, p_labels, p_bboxes, p_scores):
    """
    Intermediate function that takes the classes, class indexes, bboxes, and scores predicted by the yolo model and creates a list of the text objects to read.
    """
    numPreds = len(p_classes)
    textToRead = []
    for i in range(numPreds):
        if p_classes[i] == 'text':
            textToRead.append((i, p_bboxes[i][0], p_bboxes[i][1], p_bboxes[i][2], p_bboxes[i][3]))
    return textToRead

def _get_yolo_predictions(images, yolo_model):
    """
    Intermediate function that takes a list of images and the yolo model and gets the predicted classes, class indexes, bboxes, and confidence scores for each image in a list.
    """
    global device, YOLO_CLASSES

    results = yolo_model(images)
    toReturn = list()

    for i in range(len(results.xyxy)):
        out = results.xyxy[i]
    
        pred_classes = [YOLO_CLASSES[int(i)] for i in out[:, 5].cpu().numpy()]
        pred_bboxes = out[:, :4].detach().cpu().numpy()
        pred_scores = out[:, 4].detach().cpu().numpy()
        pred_labels = out[:, 5].detach().cpu().numpy()

        toReturn.append((pred_classes, pred_labels, pred_bboxes, pred_scores))

    return toReturn

def decode_diagram_image(images, yolo_model, text_model, converter, AlignCollate_demo, object_thresh=0.5):
    """
    Function that detects nodes within a diagram image and reads the text nodes.
    Parameters:
        - images: list of PIL image objects in RGB format
        - yolo_model: yolo model object generated by the create_yolo_model() function
        - text_model: TRBA model object generated by the create_text_model() function
        - converter: TRBA attention prediction converter object generated by the create_text_model() function
        - AlignCollate_demo: TRBA collater object generated by the create_text_model() function
        - object_thresh: threshold to cut off objects that are not above this thresh.
    Returns:
        - A list of tuples. One tuple per prediction in the image. 
            If the prediction is text the tuple is of form: (class name, class index, bbox, text detection score, predicted text, text reading score).
            If the prediction is not text the tuple is of form: (class name, class index, bbox, text detection score).
    """
    # images should be a PIL Image object in RGB format
    results = _get_yolo_predictions(images, yolo_model)
    opt = Options(text_model)
    outcomes = list()
    for i in range(len(results)):
        classes, labels, bboxes, scores = results[i]
        image = images[i]
        toBeRead = _determine_text_to_be_read(classes, labels, bboxes, scores)
        text_indexes, text_preds, text_confs = single_img(opt, image, toBeRead, converter, text_model, AlignCollate_demo, doPrint=False)

        text_indexes_set = set(text_indexes) 
        outcome = list()
        for i in range(len(classes)):
            if scores[i] >= object_thresh:
                if str(i) in text_indexes_set:
                    text_index = text_indexes.index(str(i))
                    outcome.append((classes[i], labels[i], bboxes[i], scores[i], text_preds[text_index], text_confs[text_index]))
                else:
                    outcome.append((classes[i], labels[i], bboxes[i], scores[i]))

        outcomes.append(outcome)
        
    return outcomes  

def _truncate(n, decimals=0):
    " Function to easily truncate decimals "
    multiplier = 10 ** decimals
    return int(n * multiplier) / multiplier
    
def draw_boxes(outcome, image):
    """
    Function that takes a PIL RGB image and the outcome created by the decode_diagram_image() function and returns  PIL RGB image with the bboxes, class labels, and predicted text on the image.
    """
    global COLORS
    
    # image is a PIL Image object in RGB format
    # read the image with OpenCV
    image = np.asarray(image.convert('RGB'))
    for i, tup in enumerate(outcome):
        tup_class = tup[0]
        tup_label = tup[1]
        tup_box = tup[2]
        tup_score = tup[3]

        color = COLORS[int(tup_label)]
        cv2.rectangle(
            image,
            (int(tup_box[0]), int(tup_box[1])),
            (int(tup_box[2]), int(tup_box[3])),
            color, 2
        )
        if tup_class == "text":
            cv2.putText(image, f'{i}: {tup_class} - {_truncate(tup_score, 3)}; \"{tup[4]}\" - {tup[5]}', (int(tup_box[0]), int(tup_box[1]-5)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, color, 2, 
                    lineType=cv2.LINE_AA)
        else:
            cv2.putText(image, f'{i}: {tup_class} - {_truncate(tup_score, 3)}', (int(tup_box[0]), int(tup_box[1]-5)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, color, 2, 
                    lineType=cv2.LINE_AA)
    return Image.fromarray(image)

def _get_arrow_points(grayscale, bbox):
    """
    Function that takes a cv2 grayscale image and the bounding box for an arrow and returns a tuple of the start and end points of the arrow.
    """
            # Arrow selection procedure
            # choose side with longest edge, split into two pieces
            # choose half with most black in box, split into three pieces
            # choose sixth with most black in box, we've found the end point.
            # go back to first half split, 
                # find sixth with most black, choose as start point.
                    # if final box is in same col/row as first point, switch the original end point to opposite side.
                    # compare the two sixths for who has most black, choose end/start accordingly
    image_bbox = grayscale[int(bbox[1]):int(bbox[3]), int(bbox[0]):int(bbox[2])]    
    bbox_height, bbox_width = image_bbox.shape

    transposed = False
    
    if bbox_height > bbox_width:
        transposed = True
        image_bbox = np.transpose(image_bbox)
        bbox_height, bbox_width = image_bbox.shape
        new_bbox = [bbox[1], bbox[0], bbox[3], bbox[2]]
        bbox = new_bbox

    half_height, half_width = bbox_height // 2, bbox_width // 2

    end_point = [-1, -1]
    end_point_type = ""
    start_point = [-1, -1]
    start_point_type = ""

    
    # First split is by the width
    left_sum = np.sum(image_bbox[:, :half_width])
    right_sum = np.sum(image_bbox[:, half_width:])

    if left_sum < right_sum:
        # then split by height
        cut = image_bbox[:, :half_width]
        cut_height, cut_width = cut.shape
        third_height, third_width = cut_height // 3, cut_width // 3

        top_sum = np.sum(cut[:, :third_height])
        mid_sum = np.sum(cut[:, third_height:third_height*2])
        bot_sum = np.sum(cut[:, third_height*2:])

        if mid_sum < top_sum and mid_sum < bot_sum: 
            # put end point in middle of left side!!
            end_point = [int(bbox[0]), int(bbox[1]) + bbox_height//2]
            end_point_type = "mid"
        elif top_sum < bot_sum:
            # put end point in top of left side!!
            end_point = [int(bbox[0]), int(bbox[1])] 
            end_point_type = "top"
        else:
            # put end point in bot of left side!!
            end_point = [int(bbox[0]), int(bbox[3])] 
            end_point_type = "bot"


        other_cut = image_bbox[:, half_width:]
        other_cut_height, other_cut_width = other_cut.shape
        other_third_height, other_third_width = other_cut_height // 3, other_cut_width // 3

        top_sum = np.sum(other_cut[:, :other_third_height])
        mid_sum = np.sum(other_cut[:, other_third_height:other_third_height*2])
        bot_sum = np.sum(other_cut[:, other_third_height*2:])

        if mid_sum < top_sum and mid_sum < bot_sum: 
            # put start point in middle of right side!!
            start_point = [int(bbox[2]), int(bbox[1]) + bbox_height//2]
            start_point_type = "mid"
        elif top_sum < bot_sum:
            # put end point in top of right side!!
            start_point = [int(bbox[2]), int(bbox[1])] 
            start_point_type = "top"
        else:
            # put end point in bot of right side!!
            start_point = [int(bbox[2]), int(bbox[3])] 
            start_point_type = "bot"
        
    else:
        # then split by height
        cut = image_bbox[:, half_width:]
        cut_height, cut_width = cut.shape
        third_height, third_width = cut_height // 3, cut_width // 3
        top_sum = np.sum(cut[:, :third_height])
        mid_sum = np.sum(cut[:, third_height:third_height*2])
        bot_sum = np.sum(cut[:, third_height*2:])

        if mid_sum < top_sum and mid_sum > bot_sum: 
            # put end point in middle of right side!!
            end_point = [int(bbox[2]), int(bbox[1]) + bbox_height//2]
            end_point_type = "mid"
        elif top_sum < bot_sum:
            # put end point in top of right side!!
            end_point = [int(bbox[2]), int(bbox[1])] 
            end_point_type = "top"
        else:
            # put end point in bot of right side!!
            end_point = [int(bbox[2]), int(bbox[3])] 
            end_point_type = "bot"

        other_cut = image_bbox[:, :half_width]
        other_cut_height, other_cut_width = other_cut.shape
        other_third_height, other_third_width = other_cut_height // 3, other_cut_width // 3

        top_sum = np.sum(other_cut[:, :other_third_height])
        mid_sum = np.sum(other_cut[:, other_third_height:other_third_height*2])
        bot_sum = np.sum(other_cut[:, other_third_height*2:])

        if mid_sum < top_sum and mid_sum > bot_sum: 
            # put start point in middle of left side!!
            start_point = [int(bbox[0]), int(bbox[1]) + bbox_height//2]
            start_point_type = "mid"
        elif top_sum < bot_sum:
            # put end point in top of left side!!
            start_point = [int(bbox[0]), int(bbox[1])] 
            start_point_type = "top"
        else:
            # put end point in bot of left side!!
            start_point = [int(bbox[0]), int(bbox[3])] 
            start_point_type = "bot"


    if transposed:
        return (start_point[1], start_point[0]), (end_point[1], end_point[0])
    else:
        return (start_point[0], start_point[1]), (end_point[0], end_point[1])

def draw_digital_diagram(outcome, image):
    grayscale = np.asarray(image.convert('L'))
    image = np.asarray(image.convert('RGB'))
    new_image = np.full(image.shape, 255, dtype=image.dtype)
    for i, tup in enumerate(outcome):
        tup_class = tup[0]
        tup_label = tup[1]
        tup_box = tup[2]
        tup_score = tup[3]

        color = COLORS[int(tup_label)]
        if tup_class == "text":
            desired_width = int(tup_box[2]) - int(tup_box[0])
            desired_height = int(tup_box[3]) - int(tup_box[1])
            
            # Compute correct text scale multiplier
            text_width, text_height = cv2.getTextSize(text=tup[4], fontFace=cv2.FONT_HERSHEY_SIMPLEX, fontScale=1, thickness=2)[0]
            font_scale = min(desired_width / text_width, desired_height / text_height)

            # Compute correct starting point: bottom left corner
            text_width, text_height = cv2.getTextSize(text=tup[4], fontFace=cv2.FONT_HERSHEY_SIMPLEX, fontScale=font_scale, thickness=2)[0]
            final_x_start = int(tup_box[0]) + ((desired_width - text_width) // 2)
            final_y_start = int(tup_box[3]) - ((desired_height - text_height) // 2)

            cv2.putText(new_image, tup[4], (final_x_start, final_y_start),
                    cv2.FONT_HERSHEY_SIMPLEX, font_scale, color, 2, 
                    lineType=cv2.LINE_AA)
        elif tup_class == "rectangle":
            cv2.rectangle(
                new_image,
                (int(tup_box[0]), int(tup_box[1])),
                (int(tup_box[2]), int(tup_box[3])),
                color, 2
            )
        elif tup_class == "circle":
            # Compute ellipse params
            circle_width = (int(tup_box[2]) - int(tup_box[0])) // 2
            circle_height = (int(tup_box[3]) - int(tup_box[1])) // 2
            center_x = int(tup_box[0]) + circle_width
            center_y = int(tup_box[1]) + circle_height
            cv2.ellipse(new_image, (center_x, center_y), (circle_width, circle_height), angle=0, startAngle=0, endAngle=360, color=color, thickness=2)
        elif tup_class == "parallelogram":
            # Polygon corner points coordinates
            box_width = int(tup_box[2]) - int(tup_box[0])
            box_height = int(tup_box[3]) - int(tup_box[1])
            offset = box_width // 6
            pts = np.array([[tup_box[0] + offset, tup_box[1]], # upper left point
                            [tup_box[2], tup_box[1]], # upper right point
                            [tup_box[2] - offset, tup_box[3]], # bottom right point
                            [tup_box[0], tup_box[3]]], # bottom left point
                           np.int32)
            pts = pts.reshape((-1, 1, 2))
            cv2.polylines(
                new_image, 
                [pts], 
                isClosed=True, color=color, thickness=2
            )
        elif tup_class == "diamond":
            # Polygon corner points coordinates
            center_x = int(tup_box[0]) + (int(tup_box[2]) - int(tup_box[0])) // 2
            center_y = int(tup_box[1]) + (int(tup_box[3]) - int(tup_box[1])) // 2
            pts = np.array([[center_x, tup_box[1]], # top middle point
                            [tup_box[2], center_y], # right point
                            [center_x, tup_box[3]], # bottom middle point
                            [tup_box[0], center_y]], # left point
                           np.int32)
            pts = pts.reshape((-1, 1, 2))
            cv2.polylines(
                new_image, 
                [pts], 
                isClosed=True, color=color, thickness=2
            )
        elif tup_class == "arrow":
            start_point, end_point = _get_arrow_points(grayscale, tup_box)
            cv2.arrowedLine(
                new_image,
                start_point,
                end_point,
                color, 2
            )
        else:
            print("ERROR shape not in regular shapes!")
    return Image.fromarray(new_image)