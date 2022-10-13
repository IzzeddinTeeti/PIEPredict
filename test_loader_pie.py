"""
This is to create a PIE class to load the data and generate the data for training and testing.
"""
import os


import os
import numpy as np
import torch
from torch.utils.data import Dataset
from pie_data import PIE
import utils
import sys
import argparse
# import wandb
# wandb.login()
import logging
logging.basicConfig(filename='test.log', filemode='w', level=logging.INFO)


class PIEdataset(Dataset):
    def __init__(self, args, split="train"):
        """
        Args:
            data_path (string): Path to the data directory.
            split (string): train, val, or test.
        """
        self.root = args.data_root
        self.split = split
        self.args = args
        logging.info(self.root, self.split)

        # NOTE: add downsample function
        self.PIE_FREQ = 30
        self.downsample_step = int(self.PIE_FREQ / self.args.standard_freq)
        traj_data_opts = {
            "fstride": 1,
            "sample_type": "all",
            "height_rng": [0, float("inf")],
            "squarify_ratio": 0,
            "data_split_type": "default",  # kfold, random, default
            "seq_type": "trajectory",
            "min_track_size": 61,
            "random_params": {"ratios": None, "val_data": True, "regen_data": True},
            "kfold_params": {"num_folds": 5, "fold": 1},
        }

        pred_frames = int(self.args.Prediction_length * self.PIE_FREQ)
        traj_model_opts = {
            "normalize_bbox": True,
            "track_overlap": 0.5,
            "observe_length": 15,
            "predict_length": pred_frames,
            "enc_input_type": ["bbox"],
            "dec_input_type": [],
            "prediction_type": ["bbox"],
        }
        imdb = PIE(data_path=self.root)

        # traj_model_opts["enc_input_type"].extend(["obd_speed", "heading_angle"]) # we only need bounding boxes, not speed and heading angle
        # traj_model_opts["prediction_type"].extend(["obd_speed", "heading_angle"])
        beh_seq = imdb.generate_data_trajectory_sequence(self.split, **traj_data_opts)
        self.data = self.get_traj_data(beh_seq, **traj_model_opts)

    def __getitem__(self, index):
        obs_bbox = torch.FloatTensor(self.data['obs_bbox'][index])
        pred_bbox = torch.FloatTensor(self.data['pred_bbox'][index])
        cur_image_file = self.data['obs_image'][index][-1]


        ret = {'input_x':obs_bbox,
               'target_y':pred_bbox, 
               'cur_image_file':cur_image_file}
        
        ret['timestep'] = int(cur_image_file.split('/')[-1].split('.')[0])
        
        return ret

    def __len__(self):
        return len(self.data[list(self.data.keys())[0]])

    def get_traj_tracks(self, dataset, data_types, observe_length, predict_length, overlap, normalize):
        """
        Generates tracks by sampling from pedestrian sequences
        :param dataset: The raw data passed to the method
        :param data_types: Specification of types of data for encoder and decoder. Data types depend on datasets. e.g.
        JAAD has 'bbox', 'ceneter' and PIE in addition has 'obd_speed', 'heading_angle', etc.
        :param observe_length: The length of the observation (i.e. time steps of the encoder)
        :param predict_length: The length of the prediction (i.e. time steps of the decoder)
        :param overlap: How much the sampled tracks should overlap. A value between [0,1) should be selected
        :param normalize: Whether to normalize center/bounding box coordinates, i.e. convert to velocities. NOTE: when
        the tracks are normalized, observation length becomes 1 step shorter, i.e. first step is removed.
        :return: A dictinary containing sampled tracks for each data modality
        """
        #  Calculates the overlap in terms of number of frames
        seq_length = observe_length + predict_length
        overlap_stride = observe_length if overlap == 0 else \
            int((1 - overlap) * observe_length)
        overlap_stride = 1 if overlap_stride < 1 else overlap_stride

        #  Check the validity of keys selected by user as data type
        d = {}
        for dt in data_types:
            try:
                d[dt] = dataset[dt]
            except:# KeyError:
                raise KeyError('Wrong data type is selected %s' % dt)
        
        d['image'] = dataset['image']
        d['pid'] = dataset['pid']
        # print(dataset.keys())
        # d['resolution'] = [1920, 1080] # dataset['resolution']
        d['flow'] = []
        num_trks = len(d['image'])
        # print(d['resolution'])
        #  Sample tracks from sequneces
        for k in d.keys():
            print(k, len(d[k]))
            tracks = []
            for track in d[k]:
                # print(len(track))
                for i in range(0, len(track) - seq_length + 1, overlap_stride):
                    tracks.append(track[i:i + seq_length])
            d[k] = tracks
        #  Normalize tracks using FOL paper method, 
        # d['bbox'] = self.convert_normalize_bboxes(d['bbox'], d['resolution'], 
        #                                           self.args.normalize, self.args.bbox_type)
        # d['bbox'] = self.convert_normalize_bboxes(d['bbox'], [1920, 1080], 
        #                                           self.args.normalize, self.args.bbox_type)
        print(d['bbox'][1])
        return d

    def convert_normalize_bboxes(self, all_bboxes, all_resolutions, normalize, bbox_type):
        '''input box type is x1y1x2y2 in original resolution'''
        for i in range(len(all_bboxes)):
            if len(all_bboxes[i]) == 0:
                continue
            bbox = np.array(all_bboxes[i])
            # NOTE ltrb to cxcywh
            if bbox_type == 'cxcywh':
                bbox[..., [2, 3]] = bbox[..., [2, 3]] - bbox[..., [0, 1]]
                bbox[..., [0, 1]] += bbox[..., [2, 3]]/2
            # NOTE Normalize bbox
            if normalize == 'zero-one':
                # W, H  = all_resolutions[i][0]
                _min = np.array(self.args.min_bbox)[None, :]
                _max = np.array(self.args.max_bbox)[None, :]
                bbox = (bbox - _min) / (_max - _min)
            elif normalize == 'plus-minus-one':
                # W, H  = all_resolutions[i][0]
                _min = np.array(self.args.min_bbox)[None, :]
                _max = np.array(self.args.max_bbox)[None, :]
                bbox = (2 * (bbox - _min) / (_max - _min)) - 1
            elif normalize == 'none':
                pass
            else:
                raise ValueError(normalize)
            all_bboxes[i] = bbox
        return all_bboxes

    def get_data_helper(self, data, data_type):
        """
        A helper function for data generation that combines different data types into a single representation
        :param data: A dictionary of different data types
        :param data_type: The data types defined for encoder and decoder input/output
        :return: A unified data representation as a list
        """
        if not data_type:
            return []
        d = []
        for dt in data_type:
            if dt == 'image':
                continue
            d.append(np.array(data[dt]))
            
        #  Concatenate different data points into a single representation
        if len(d) > 1:
            return np.concatenate(d, axis=2)
        elif len(d) == 1:
            return d[0]
        else:
            return d
            
    def get_traj_data(self, data, **model_opts):
        """
        Main data generation function for training/testing
        :param data: The raw data
        :param model_opts: Control parameters for data generation characteristics (see below for default values)
        :return: A dictionary containing training and testing data
        """
        
        opts = {
            'normalize_bbox': True,
            'track_overlap': 0.5,
            'observe_length': 15, # number of frames, can be converted to input
            'predict_length': 45, # number of frames, can be converted to input
            'enc_input_type': ['bbox'],
            'dec_input_type': [],
            'prediction_type': ['bbox']
        }
        for key, value in model_opts.items():
            assert key in opts.keys(), 'wrong data parameter %s' % key
            opts[key] = value

        observe_length = opts['observe_length']
        predict_length = opts['predict_length']
        data_types = set(opts['enc_input_type'] + opts['dec_input_type'] + opts['prediction_type'])
        data_tracks = self.get_traj_tracks(data, data_types, observe_length,
                                      opts['predict_length'], opts['track_overlap'],
                                      opts['normalize_bbox'])
        obs_slices = {}
        pred_slices = {}
        #  Generate observation/prediction sequences from the tracks
        for k in data_tracks.keys():
            obs_slices[k] = []
            pred_slices[k] = []
            # NOTE: Add downsample function
            down = self.downsample_step
            obs_slices[k].extend([d[down-1:observe_length:down] for d in data_tracks[k]])
            if k == 'bbox':
                start = down-1 # 0 
                end = start + observe_length # 0 + 15 = 15
                target_list = [] # 15 * 45 * 4

                for d in data_tracks[k]:
                    target = self.get_target(d,start,end,observe_length,predict_length)
                    target_list.append(target)
                pred_slices[k].extend(target_list)
        ret =  {'obs_image': obs_slices['image'],
                'obs_pid': obs_slices['pid'],
                'obs_resolution': obs_slices['resolution'],
                'pred_image': pred_slices['image'],
                'pred_pid': pred_slices['pid'],
                'pred_resolution': pred_slices['resolution'],
                'obs_bbox': np.array(obs_slices['bbox']), #enc_input,
                'pred_bbox': np.array(pred_slices['bbox']), #pred_target,
                }
        
        return ret
    
    def get_path(self,
                 file_name='',
                 save_folder='models',
                 dataset='pie',
                 model_type='trajectory',
                 save_root_folder='data/'):
        """
        A path generator method for saving model and config data. It create directories if needed.
        :param file_name: The actual save file name , e.g. 'model.h5'
        :param save_folder: The name of folder containing the saved files
        :param dataset: The name of the dataset used
        :param save_root_folder: The root folder
        :return: The full path for the model name and the path to the final folder
        """
        save_path = os.path.join(save_root_folder, dataset, model_type, save_folder)
        if not os.path.exists(save_path):
            os.makedirs(save_path)
        return os.path.join(save_path, file_name), save_path

    def get_target(self, session, start, end, observe_length, predict_length):
        '''
        Given the input session and the start and end time of the input clip, find the target
        TARGET FOR PREDICTION IS THE CHANGES IN THE FUTURE!!
        Params:
            session: the input time sequence of a car, can be bbox or ego_motion with shape (time, :)
            start: start frame id 
            end: end frame id
        Returns:
            target: Target tensor with shape (self.args.segment_len, dec_steps, :)
                    The target is the change of the values. e.g. target of yaw is \delta{\theta}_{t0,tn} 
        ''' 
        target = np.zeros((observe_length, predict_length, session.shape[-1]))
        for i, target_start in enumerate(range(start, end)):
            '''the target of time t is the change of bbox/ego motion at times [t+1,...,t+5}'''
            # i, target_start = (0,0) (1,1) (2,2) ......
            target_start = target_start + 1
            try:
                target[i,:,:] = np.asarray(session[target_start:target_start+predict_length,:] - 
                                           session[target_start-1:target_start,:])
            except:
                print("segment start: ", start)
                print("sample start: ", target_start)
                print("segment end: ", end)
                print(session.shape)
                raise ValueError()
        return target


parser = argparse.ArgumentParser(
    description="Convert pointclouds from .pcd to .bin and the annotation from .json to .txt KITTI format"
)
parser.add_argument(
    "--data_root",
    help="Supervisely folder path that contains pointcloud folder, json folder, and meta file",
    type=str,
    default="/mnt/mars-beta/izzeddin/PIEPredict/PIE_dataset",
)
parser.add_argument(
    "--standard_freq",
    help="KITTI folder path that will contain bin folder and txt folder",
    type=str,
    default=12,  #  /mnt/mars-beta/izzeddin/MAESTRO/testing ann
)
parser.add_argument(
    "--Prediction_length",
    help="Prediction length in seconds",
    type=str,
    default=0.5,  #  /mnt/mars-beta/izzeddin/MAESTRO/testing ann
)

# loggging = wandb.init(project="PIE", entity="izzeddin", id="test", resume=None, name='test')
# config = loggging.config
args = parser.parse_args()

# utils.setup_logger(args)
# logger = utils.get_logger(__name__)
# logger.info(sys.version)

print(args.data_root)

dataset = PIEdataset(args, split="train")
