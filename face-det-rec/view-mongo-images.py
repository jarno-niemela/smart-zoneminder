"""
This program will fetch documents from the Mongodb collection written to by zm-s3-uploader.js.
Then face detection / recognition can be applied by stepping through the stored images.
This is useful to tune the face detection / recognition parameters. 

Copyright (c) 2019 Lindo St. Angel
"""

import face_recognition
import argparse
import pickle
import cv2
import json
import numpy as np
from shutil import copy
from pymongo import MongoClient
from bson import json_util

# Set to True if using SVM face classifier else knn will be used.
USE_SVM_CLASS = True

# Settings for SVM classifier.
# The model and label encoder needs to be generated by 'train.py' first. 
SVM_MODEL_PATH = '/home/lindo/develop/smart-zoneminder/face-det-rec/face_recognizer.pickle'
SVM_LABEL_PATH = '/home/lindo/develop/smart-zoneminder/face-det-rec/face_labels.pickle'
MIN_SVM_PROBA = 0.8

# Settings for knn face classifier.
# Known face encodings.
# The pickle file needs to be generated by the 'encode_faces.py' program first.
KNOWN_FACE_ENCODINGS_PATH = '/home/lindo/develop/smart-zoneminder/face-det-rec/encodings.pickle' 
# Face comparision tolerance. Only used for knn face classifier. 
# A lower value causes stricter compares which may reduce false positives.
# See https://github.com/ageitgey/face_recognition/wiki/Face-Recognition-Accuracy-Problems.
COMPARE_FACES_TOLERANCE = 0.60
# Threshold to declare a valid face.
# This is the percentage of all embeddings for a face name. 
NAME_THRESHOLD = 0.25

# Where to save images and metadata of examined data. 
SAVE_PATH = '/home/lindo/develop/smart-zoneminder/face-det-rec/saved_images/'

# Face detection model to use. Can be either 'cnn' or 'hog'
FACE_DET_MODEL = 'cnn'

# How many times to re-sample the face when calculating face encoding.
NUM_JITTERS = 100

# url of mongodb database that zm-s3-upload.js uses.
MONGO_URL = 'mongodb://zmuser:zmpass@localhost:27017/?authSource=admin'

# Number of documents to fetch from the mongodb database.
NUM_ALARMS = 4000

# Object detection confidence threshold.
IMAGE_MIN_CONFIDENCE = 60

# Images with Variance of Laplacian less than this are declared blurry. 
FOCUS_MEASURE_THRESHOLD = 200

# Set to True to see most recent alarms first.
IMAGE_DECENDING_ORDER = False

# Key codes on my system for cv2.waitKeyEx().
ESC_KEY = 1048603
RIGHT_ARROW_KEY = 1113939
LEFT_ARROW_KEY = 1113937
UP_ARROW_KEY = 1113938
DOWN_ARROW_KEY = 1113940
SPACE_KEY = 1048608
LOWER_CASE_Q_KEY = 1048689
LOWER_CASE_S_KEY = 1048691
LOWER_CASE_P_KEY = 1048688
LOWER_CASE_O_KEY = 1048687

# Settings to save images and metadata in Pascal VOC format. 
PVOC_IMG_WIDTH = 300
PVOC_IMG_HEIGHT = 300
PVOC_IMG_BASE_PATH = '/home/lindo/develop/tensorflow/models/images/'
PVOC_XML_BASE_PATH = '/home/lindo/develop/tensorflow/models/annotations/xmls/'

def variance_of_laplacian(image):
	# compute the Laplacian of the image and then return the focus
	# measure, which is simply the variance of the Laplacian
	return cv2.Laplacian(image, cv2.CV_64F).var()

def generate_xml(image_path, image_shape, orig_h, orig_w, image_labels):
	# generate xml from the alarm image metadata
	# in Pascal VOC format
	path_list = image_path.split('/')
	h, w, d = image_shape
	# scale factors to adjust original bounding box to resized image
	h_scale = h/orig_h
	w_scale = w/orig_w

	xml = '<annotation>\n'
	xml += '\t<folder>' + path_list[-2] + '</folder>\n'
	xml += '\t<filename>' + path_list[-1] + '</filename>\n'
	xml += '\t<path>' + image_path + '</path>\n'
	xml += '\t<source>\n\t\t<database>Unknown</database>\n\t</source>\n'
	xml += '\t<size>\n'
	xml += '\t\t<width>' + str(w) + '</width>\n'
	xml += '\t\t<height>' + str(h) + '</height>\n'
	xml += '\t\t<depth>' + str(d) + '</depth>\n'
	xml += '\t</size>\n'
	xml += '\t<segmented>0</segmented>\n'
	for label in image_labels:
		xml += '\t<object>\n'
		xml += '\t\t<name>' + label['Face'] + '</name>\n'
		xml += '\t\t<pose>Unspecified</pose>\n'
		xml += '\t\t<truncated>1</truncated>\n'
		xml += '\t\t<difficult>0</difficult>\n'
		xml += '\t\t<bndbox>\n'
		xml += '\t\t\t<xmin>' + str(int(label['Box']['xmin'] * w_scale)) + '</xmin>\n'
		xml += '\t\t\t<xmax>' + str(int(label['Box']['xmax'] * w_scale)) + '</xmax>\n'
		xml += '\t\t\t<ymin>' + str(int(label['Box']['ymin'] * h_scale)) + '</ymin>\n'
		xml += '\t\t\t<ymax>' + str(int(label['Box']['ymax'] * h_scale)) + '</ymax>\n'
		xml += '\t\t</bndbox>\n'
		xml += '\t</object>\n'
	xml += '</annotation>'

	return xml

def knn_face_classifier(encoding, compare_face_tolerance, name_threshold, name_count):
	# attempt to match each face in the input image to our known encodings
	matches = face_recognition.compare_faces(data['encodings'],
		encoding, compare_face_tolerance)

	# Assume face is unknown to start with. 
	name = 'Unknown'

	# check to see if we have found a match
	if True in matches:
		# find the indexes of all matched faces then initialize a
		# dictionary to count the total number of times each face
		# was matched
		matchedIdxs = [i for (i, b) in enumerate(matches) if b]

		# init all name counts to 0
		counts = {n: 0 for n in data['names']}
		#print('initial counts {}'.format(counts))

		# loop over the matched indexes and maintain a count for
		# each recognized face
		for i in matchedIdxs:
			name = data['names'][i]
			counts[name] = counts.get(name, 0) + 1
		#print('counts {}'.format(counts))

		# Find face name with the max count value.
		max_value = max(counts.values())
		max_name = max(counts, key=counts.get)

		# Compare each recognized face against the max face name.
		# The max face name count must be greater than a certain value for
		# it to be valid. This value is set at a percentage of the number of
		# embeddings for that face name. 
		name_thresholds = [max_value > value + name_threshold * name_count[max_name]
			for name, value in counts.items() if name != max_name]

		# If max face name passes against all other faces then declare it valid.
		if all(name_thresholds):
			name = max_name
			print('kkn says this is {}'.format(name))
		else:
			name = None
			print('kkn cannot recognize face')

	return name


def svm_face_classifier(encoding, min_proba):
	# perform svm classification to recognize the face based on 128D encoding
	# note: reshape(1,-1) converts 1D array into 2D
	preds = recognizer.predict_proba(encoding.reshape(1, -1))[0]
	j = np.argmax(preds)
	proba = preds[j]
	#print('svm proba {} name {}'.format(proba, le.classes_[j]))

	if proba >= min_proba:
		name = le.classes_[j]
		print('svm says this is {}'.format(name))
	else:
		name = None # prob too low to recog face
		print('svm cannot recognize face')

	return name

if USE_SVM_CLASS is True:
	# load the actual face recognition model along with the label encoder
	with open(SVM_MODEL_PATH, 'rb') as fp:
		recognizer = pickle.load(fp)
	with open(SVM_LABEL_PATH, 'rb') as fp:
		le = pickle.load(fp)
else:
	# Load the known faces and embeddings.
	with open(KNOWN_FACE_ENCODINGS_PATH, 'rb') as fp:
		data = pickle.load(fp)
	# Calculate number of embeddings for each face name.
	name_count = {n: 0 for n in data['names']}
	for name in data['names']:
		name_count[name] += 1
	#print(name_count)

client = MongoClient(MONGO_URL)

alarms = []

# Query database for person objects.
# Return documents in descending order and limit.
with client:
	db = client.zm
	alarms = list(
		db.alarms.find(
			#{'labels.Labels.Name' : 'person'} # old database format
			#{'labels.Name' : 'person'}
			{'labels.Face' : 'eva_st_angel'}
		).sort([('_id', -1)]).limit(NUM_ALARMS)
	)

# Since alarms are in decending order by default reverse list to see earlist alarms first.
if not IMAGE_DECENDING_ORDER:
	alarms.reverse()

# Create a window that can be resized by the user.
# TODO: figure out why I cannot resize window using the mouse
cv2.namedWindow('face detection results', cv2.WINDOW_NORMAL)

idx = 0 # index of alarm image being processed
pvoc_counter = 80 # counter used to save images in Pascal VOC format

while True:
	alarm = alarms[idx]

	print('===== New Image =====')
	print(alarm)

	img = cv2.imread(alarm['image'])
	if img is None:
		print('Alarm image not found...skipping.')
		idx += 1
		if idx > len(alarms) - 1:
			print('Reached end of alarm images...exiting.')
			break
		continue

	height, width, channels = img.shape
	print('image height {} width {} channels {}'.format(height, width, channels))

	labels = alarm['labels']

	# Find all roi's in image, then look for faces in the rois, then show faces on the image.
	for object in labels:
		if object['Name'] == 'person' and object['Confidence'] > IMAGE_MIN_CONFIDENCE:
			print('Found person object...')
			# (0, 0) is the top left point of the image.
			# (x1, y1) are the top left roi coordinates.
			# (x2, y2) are the bottom right roi coordinates.
			y2 = int(object['Box']['ymin'])
			x1 = int(object['Box']['xmin'])
			y1 = int(object['Box']['ymax'])
			x2 = int(object['Box']['xmax'])

			roi = img[y2:y1, x1:x2, :]
			if roi.size == 0:
				continue
			#cv2.imshow('roi', roi)
			#cv2.waitKey(0)

			# detect the (x, y)-coordinates of the bounding boxes corresponding
			# to each face in the input image
			# Do not increase upsample. System will crash from out of memory.
			rgb = cv2.cvtColor(roi, cv2.COLOR_BGR2RGB)
			box = face_recognition.face_locations(rgb, number_of_times_to_upsample=1,
				model=FACE_DET_MODEL)

			# initialize the list of names for each face detected
			names = []

			if not box:
				print('no face detected...skipping face rec')
				names = [None]
			else:
				# Carve out face roi from object roi. 
				face_top, face_right, face_bottom, face_left = box[0]
				face_roi = roi[face_top:face_bottom, face_left:face_right, :]
				#cv2.imshow('face roi', face_roi)
				#cv2.waitKey(0)

				# Compute the focus measure of the face
				# using the Variance of Laplacian method.
				# See https://www.pyimagesearch.com/2015/09/07/blur-detection-with-opencv/
				gray = cv2.cvtColor(face_roi, cv2.COLOR_BGR2GRAY)
				fm = variance_of_laplacian(gray)
				print('fm {}'.format(fm))

				if fm < FOCUS_MEASURE_THRESHOLD:
					# If fm below a threshold then face probably isn't clear enough
					# for face recognition to work, so just skip it. 
					print('face is blurred...skipping face rec')
					names = [None]
				else:
					# Return the 128-dimension face encoding for face in the image.
					# TODO - figure out why encodings are slightly different in
					# face_det_rec.py for same image
					encoding = face_recognition.face_encodings(rgb, box, NUM_JITTERS)[0]

					if USE_SVM_CLASS is True:
						# perform svm classification to recognize the face
						name = svm_face_classifier(encoding, MIN_SVM_PROBA)
					else:
						# perform knn classification to recognize the face
						name = knn_face_classifier(encoding, COMPARE_FACES_TOLERANCE,
							NAME_THRESHOLD, name_count)

					print('name {}'.format(name))
					# update the list of names
					names.append(name)
			
			# Draw the object roi and its label on the image.
			# This must be done after fm calc otherwise drawing edge will affect result. 
			cv2.rectangle(img, (x1, y1), (x2, y2), (255,0,0), 2)
			cv2.putText(img, 'person', (x1, y2 - 15), cv2.FONT_HERSHEY_SIMPLEX, 0.75, (0, 255, 0), 2)

			# Loop over the recognized faces and annotate image. 
			for ((top, right, bottom, left), name) in zip(box, names):
				#print('face box top {} right {} bottom {} left {}'.format(top, right, bottom, left))
				face_box_width = right - left
				face_box_height = bottom - top
				print('face box width {} height {}'.format(face_box_width, face_box_height))
				# draw the predicted face box and put name on the image
				cv2.rectangle(img, (left + x1, top + y2), (right + x1, bottom + y2), (0, 255, 0), 2)
				y = top + y2 - 15 if top + y2 - 15 > 15 else top + y2 + 15
				cv2.putText(img, name, (left + x1, y), cv2.FONT_HERSHEY_SIMPLEX, 0.75, (0, 255, 0), 2)
				# put originally predicted name on image as well if it differs from new predicted name
				# if different name it will be shown in red text
				if name != object['Face']:
					y = bottom + y2 + 15 if bottom + y2 + 15 > 15 else bottom + y2 - 15
					cv2.putText(img, object['Face'], (left + x1, y), cv2.FONT_HERSHEY_SIMPLEX, 0.75, (0, 0, 255), 2)

	cv2.imshow('face detection results', img)

	key = cv2.waitKeyEx()
	#print('key press {}'.format(key))
	if key == LOWER_CASE_Q_KEY or key == ESC_KEY: # exit program
		break
	if key == LOWER_CASE_S_KEY: # save current image and metadata
		obj_id_str = str(alarm['_id'])
		print('Saving current alarm with id {}.'.format(obj_id_str))
		cv2.imwrite(SAVE_PATH + obj_id_str + '.jpg', img)
		json_dumps = json.dumps(alarm, default=json_util.default)
		with open(SAVE_PATH + obj_id_str + '.json', 'w') as outfile:
			json.dump(json_dumps, outfile)
	elif key == LOWER_CASE_O_KEY: # save image w/o annotations
		image_path = alarm['image']
		print('Saving original alarm image with path {}.'.format(image_path))
		copy(image_path, SAVE_PATH)
	elif key == LOWER_CASE_P_KEY: # save image w/Pascal VOC metadata
		local_image_path = alarm['image']
		print('Saving image and Pascal VOC metadata for {}.'.format(local_image_path))
		# get original image w/o annotations
		image = cv2.imread(local_image_path)
		# resize image and store it
		(width,height) = (PVOC_IMG_WIDTH, PVOC_IMG_HEIGHT)
		resized_image = cv2.resize(image,(width, height),interpolation=cv2.INTER_AREA)
		resized_image_path = PVOC_IMG_BASE_PATH + str(pvoc_counter) + '.jpg'
		cv2.imwrite(resized_image_path, resized_image)
		# generate xml metadata for image and store it
		oh, ow, od = image.shape # original image shape
		# take only person objects with identified faces from alarm images
		faces = [face for face in alarm['labels'] if face.get('Face') is not None]
		xml = generate_xml(resized_image_path, resized_image.shape, oh, ow, faces)
		with open(PVOC_XML_BASE_PATH + str(pvoc_counter) + '.xml', 'w') as outfile:
			outfile.write(xml)
		pvoc_counter += 1
	elif key == LEFT_ARROW_KEY or key == DOWN_ARROW_KEY: # go back
		idx -= 1
	elif key == SPACE_KEY or key == RIGHT_ARROW_KEY or key == UP_ARROW_KEY: # advance
		idx += 1
		if idx > len(alarms) - 1:
			print('Reached end of alarm images...exiting.')
			break

cv2.destroyAllWindows()