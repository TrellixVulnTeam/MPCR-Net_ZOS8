import open3d as o3d
import argparse
import os
import sys
import logging
import numpy
import numpy as np
import torch
import torch.utils.data
import torchvision
from torch.utils.data import DataLoader
from tensorboardX import SummaryWriter
from tqdm import tqdm

# Only if the files are in example folder.
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if BASE_DIR[-8:] == 'examples':
	sys.path.append(os.path.join(BASE_DIR, os.pardir))
	os.chdir(os.path.join(BASE_DIR, os.pardir))
	
from TPCCNet.learning3d.models.tpccnet import tpccNet
from TPCCNet.learning3d.data_utils import RegistrationData, ModelNet40Data, UserData, AnyData
from TPCCNet.registration import Registration

def pc2open3d(data):
	if torch.is_tensor(data): data = data.detach().cpu().numpy() 
	if len(data.shape) == 2:
		pc = o3d.geometry.PointCloud()
		pc.points = o3d.utility.Vector3dVector(data)
		return pc
	else:
		print("Error in the shape of data given to Open3D!, Shape is ", data.shape)

def display_results(template, source, est_T, tpcced_template):
	transformed_source = np.matmul(est_T[0:3, 0:3], source.T).T + est_T[0:3, 3]

	template = pc2open3d(template)
	source = pc2open3d(source)
	transformed_source = pc2open3d(transformed_source)
	tpcced_template = pc2open3d(tpcced_template)
	
	template.paint_uniform_color([1, 0, 0])
	source.paint_uniform_color([0, 1, 0])
	transformed_source.paint_uniform_color([0, 0, 1])
	tpcced_template.paint_uniform_color([0, 0, 1])


	o3d.visualization.draw_geometries([template, source, transformed_source])

def evaluate_metrics(TP, FP, FN, TN, gt_tpcc):
	# TP, FP, FN, TN: 		True +ve, False +ve, False -ve, True -ve
	# gt_tpcc:				Ground Truth tpcc [Nt, 1]
	
	accuracy = (TP + TN)/gt_tpcc.shape[1]
	misclassification_rate = (FN + FP)/gt_tpcc.shape[1]
	# Precision: (What portion of positive identifications are actually correct?)  #Determine the accuracy of true 1 and true 0, accounting for the template point cloud
	precision = TP / (TP + FP)
	# Recall: (What portion of actual positives are identified correctly?) #Judging the accuracy of true 1, accounting for the source point cloud
	recall = TP / (TP + FN)

	fscore = (2*precision*recall) / (precision + recall)
	return accuracy, precision, recall, fscore

# Function used to evaluate the predicted tpcc with ground truth tpcc.
def evaluate_tpcc(gt_tpcc, predicted_tpcc, predicted_tpcc_idx):
	# gt_tpcc:					Ground Truth tpcc [Nt, 1]
	# predicted_tpcc:			tpcc predicted by network [Nt, 1]
	# predicted_tpcc_idx:		Point indices chosen by network [Ns, 1]

	if torch.is_tensor(gt_tpcc): gt_tpcc = gt_tpcc.detach().cpu().numpy()
	if torch.is_tensor(gt_tpcc): predicted_tpcc = predicted_tpcc.detach().cpu().numpy()
	if torch.is_tensor(predicted_tpcc_idx): predicted_tpcc_idx = predicted_tpcc_idx.detach().cpu().numpy()
	gt_tpcc, predicted_tpcc, predicted_tpcc_idx = gt_tpcc.reshape(1,-1), predicted_tpcc.reshape(1,-1), predicted_tpcc_idx.reshape(1,-1)
	
	gt_idx = np.where(gt_tpcc == 1)[1].reshape(1,-1) 				# Find indices of points which are actually in source.    np.where(gt_tpcc == 1)Output the coordinates of the elements that meet the conditions

	# TP + FP = number of source points.
	TP = np.intersect1d(predicted_tpcc_idx[0], gt_idx[0]).shape[0]			# is inliner and predicted as inlier (True Positive) 		(Find common indices in predicted_tpcc_idx, gt_idx)，Find the same value in two arrays np.intersect1d（）
	FP = len([x for x in predicted_tpcc_idx[0] if x not in gt_idx])			# isn't inlier but predicted as inlier (False Positive)  0→1
	FN = FP															# is inlier but predicted as outlier (False Negative) (due to binary classification) 1→0  The number of true 1s is equal to TP + FP = number of source points the number is fixed，So how many 0→1 the quantity corresponds to the same quantity 1→0 to maintain a fixed amount of 1
	TN = gt_tpcc.shape[1] - gt_idx.shape[1] - FN 					# is outlier and predicted as outlier (True Negative)  which is TN=（ gt_tpcc.shape[1] - gt_idx.shape[1] ）- FN    gt_tpcc.shape[1] - gt_idx.shape[1] the number of true zeros is also a fixed value, minus the false zero of the wrong judgment is equal to the zero of the right judgment
	return evaluate_metrics(TP, FP, FN, TN, gt_tpcc)

def test_one_epoch(args, model, test_loader):
	model.eval()
	test_loss = 0.0
	pred  = 0.0
	count = 0
	AP_List = []
	GT_Size_List = []
	precision_list = []
	registration_model = Registration(args.reg_algorithm)

	for i, data in enumerate(tqdm(test_loader)):  #177:test_loader= DataLoader(testset, .....)， #return template, source, igt, gt_tpcc
		template, source, igt, gt_tpcc = data

		template = template.to(args.device)
		source = source.to(args.device)
		igt = igt.to(args.device)						# [source] = [igt]*[template]
		gt_tpcc = gt_tpcc.to(args.device)

		tpcced_template, predicted_tpcc = model(template, source)  #model = tpccNet()is located in tpccnet.py, and calculated tpcced_template(C × X) and predicted_tpcc(C)
		result = registration_model.register(tpcced_template, source)   #from registration import Registration，registration_model = Registration(args.reg_algorithm)Line 172, specify the algorithm, bring tpcced_template into the calculation
		est_T = result['est_T']#The transformation matrix est_T is calculated by the specified algorithm
		 
		# Evaluate tpcc based on classification metrics.
		accuracy, precision, recall, fscore = evaluate_tpcc(gt_tpcc, predicted_tpcc, predicted_tpcc_idx = model.tpcc_idx)
		precision_list.append(precision)
		
		# Different ways to visualize results.
		display_results(template.detach().cpu().numpy()[0], source.detach().cpu().numpy()[0], est_T.detach().cpu().numpy()[0], tpcced_template.detach().cpu().numpy()[0])

	print("Mean Precision: ", np.mean(precision_list))

def test(args, model, test_loader):
	test_one_epoch(args, model, test_loader)

def options():
	parser = argparse.ArgumentParser(description='tpccNet: A Fully-Convolutional Network For Inlier Estimation (Testing)')
	parser.add_argument('--user_data', type=bool, default=False, help='Train or Evaluate the network with User Input Data.')
	parser.add_argument('--any_data', type=bool, default=True, help='Evaluate the network with Any Point Cloud.')

	# settings for input data
	parser.add_argument('--num_points', default=1024, type=int,
						metavar='N', help='points in point-cloud (default: 1024)')
	parser.add_argument('--partial_source', default=True, type=bool,
						help='create partial source point cloud in dataset.')
	parser.add_argument('--noise', default=False, type=bool,
						help='Add noise in source point clouds.')
	parser.add_argument('--outliers', default=False, type=bool,
						help='Add outliers to template point cloud.')

	# settings for on testing
	parser.add_argument('-j', '--workers', default=1, type=int,
						metavar='N', help='number of data loading workers (default: 4)')
	parser.add_argument('-b', '--test_batch_size', default=1, type=int,
						metavar='N', help='test-mini-batch size (default: 1)')
	parser.add_argument('--reg_algorithm', default='pointnetlk', type=str,
						help='Algorithm used for registration.', choices=['pointnetlk', 'icp', 'dcp', 'prnet', 'tmpenet', 'rpmnet'])
	parser.add_argument('--pretrained', default='path to pretrained model file', type=str,
						metavar='PATH', help='path to pretrained model file (default: null (no-use))')
	parser.add_argument('--device', default='cuda:0', type=str,
						metavar='DEVICE', help='use CUDA if available')
	parser.add_argument('--unseen', default=False, type=bool,
						help='Use first 20 categories for training and last 20 for testing')

	args = parser.parse_args()
	return args

def main():
	args = options()
	torch.backends.cudnn.deterministic = True

	if args.user_data:
		template = np.random.randn(1, 100, 3)					# Define/Read template point cloud. [Shape: BATCH x No. of Points x 3]  Test with personal data
		source = np.random.randn(1, 75, 3)						# Define/Read source point cloud. [Shape: BATCH x No. of Points x 3]
		tpcc = np.zeros((1, 100, 1))							# Define/Read tpcc for point cloud. [Not mandatory in testing]
		igt = np.zeros((1, 4, 4))								# Define/Read igt transformation. [Not mandatory during testing]
		testset = UserData(template=template, source=source, tpcc=None, igt=None)	
	elif args.any_data:
		# Read Stanford bunny's point cloud.
		bunny_path = os.path.join('path to  model file')
		if not os.path.exists(bunny_path): 
			print("Please download bunny dataset from http://graphics.stanford.edu/data/3Dscanrep/")
			print("Add the extracted folder in learning3d/data/")
		data = o3d.io.read_point_cloud(bunny_path)
		points = np.array(data.points)
		idx = np.arange(points.shape[0])
		np.random.shuffle(idx)
		points = points[idx[:args.num_points]]

		testset = AnyData(pc=points, tpcc=True)
	else:
		testset = RegistrationData(ModelNet40Data(train=False, num_points=args.num_points, unseen=args.unseen),
									partial_source=args.partial_source, noise=args.noise, outliers=args.outliers) 
	test_loader = DataLoader(testset, batch_size=args.test_batch_size, shuffle=False, drop_last=False, num_workers=args.workers)

	if not torch.cuda.is_available():
		args.device = 'cpu'
	args.device = torch.device(args.device)

	# Load Pretrained tpccNet.
	model = tpccNet()
	if args.pretrained:
		assert os.path.isfile(args.pretrained)
		model.load_state_dict(torch.load(args.pretrained, map_location='cpu'))
	model = model.to(args.device)

	test(args, model, test_loader)

if __name__ == '__main__':
	main()
