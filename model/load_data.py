"""Data analysis datanalysis using pytorch data loader"""
from torch.utils.data import Dataset
import os
import numpy as np
import torch
import ipdb
from pytorch3d.transforms import axis_angle_to_quaternion, axis_angle_to_matrix, matrix_to_rotation_6d
from data.data_splits import amass_splits
import glob


def quat_flip(pose_in):
    pose_flip = np.copy(pose_in)
    is_neg = pose_in[:,:,0] <0
    pose_flip[np.where(is_neg)] = (-1)*pose_flip[np.where(is_neg)]
    return pose_flip, np.where(is_neg)

class PoseData(Dataset):


    def __init__(self, mode, data_path, amass_dir, batch_size=4, num_workers=6, num_pts=5000, flip=False):
        # Modified version
        self.mode= mode
        # self.path = data_path
        self.amass_path = amass_dir
        self.num_pts = num_pts
        # self.data_samples = amass_splits[self.mode]
        self.data_files = [data_path] #negative samples, we consider a single path for now
        # self.data_files = [ds for ds in self.data_files if ds.split('/')[-2] in self.data_samples]
        self.pos_data = [amass_dir] #positive samples, we consider a single path for now
        # self.amass_files = glob.glob(self.amass_path+ '/*/*.npz')
        # self.amass_files = [ds for ds in self.amass_files if ds.split('/')[-2] in self.data_samples]

        self.batch_size = batch_size
        self.num_workers = num_workers
        self.flip= flip

    # Original version
    # def __init__(self, mode, data_path, amass_dir, batch_size=4, num_workers=6, num_pts=5000, flip=False):

    #     self.mode= mode
    #     self.path = data_path
    #     self.amass_path = amass_dir
    #     self.num_pts = num_pts
    #     self.data_samples = amass_splits[self.mode]
    #     self.data_files = glob.glob(self.path+ '/*/*000.npz')
    #     #self.data_files = glob.glob(self.path+ '/*/*.npz')
    #     self.data_files = [ds for ds in self.data_files if ds.split('/')[-2] in self.data_samples]
    #     self.amass_files = glob.glob(self.amass_path+ '/*/*.npz')
    #     self.amass_files = [ds for ds in self.amass_files if ds.split('/')[-2] in self.data_samples]

    #     self.batch_size = batch_size
    #     self.num_workers = num_workers
    #     self.flip= flip


    def __len__(self):
        return len(self.data_files)


    def __getitem__(self, idx=0):

        """read pose and distance data"""
        # Modified function to use our own data, old version commented below
        #idx defaults to 0 because we have a single path for now
        # See below how I understood the data_names
        # poses should be the non-manifold poses, i.e. negative samples
        # dist should be the ground truth distance
        # man_poses are the manifold poses, i.e. positive samples
        pose_data = np.load(self.data_files[idx])#Probably no need for idx

        # sample poses from this file 
        subsample_indices = np.random.randint(0, len(pose_data['pose']), self.num_pts)
        poses = pose_data['pose'][subsample_indices]
        if self.flip:
            poses, _  = quat_flip(poses)
        dist = np.mean(pose_data['dist'][subsample_indices],axis=1)
        # nn_pose = pose_data['nn_pose'][subsample_indices]

        #read amass_poses
        amass_idx = np.random.randint(0, len(self.pos_data), 1)
        amass_pose_data = np.load(self.pos_data[amass_idx[0]])
        # sample poses from this file 
        subsample_indices = np.random.randint(0, len(amass_pose_data['pose']), self.num_pts)
        amass_poses = amass_pose_data['pose'][subsample_indices]
        if self.flip:
            amass_poses, _  = quat_flip(poses)

        # assert not np.any(np.isnan(poses))
        # assert not np.any(np.isnan(dist))


        return {'pose': np.array(poses, dtype=np.float32),
                'dist': np.array(dist, dtype=np.float32),
                'man_poses': np.array(amass_poses, dtype=np.float32),
                #'nn_pose': np.array(nn_pose, dtype=np.float32),
                #'betas': np.array(betas, dtype=np.float32)
                }

    # Original version
    # def __getitem__(self, idx):

    #     """read pose and distance data"""
    #     pose_data = np.load(self.data_files[idx])

    #     # sample poses from this file 
    #     subsample_indices = np.random.randint(0, len(pose_data['pose']), self.num_pts)
    #     poses = pose_data['pose'][subsample_indices]
    #     if self.flip:
    #         poses, _  = quat_flip(poses)
    #     dist = np.mean(pose_data['dist'][subsample_indices],axis=1)
    #     # nn_pose = pose_data['nn_pose'][subsample_indices]

    #     #read amass_poses
    #     amass_idx = np.random.randint(0, len(self.amass_files), 1)
    #     amass_pose_data = np.load(self.amass_files[amass_idx[0]])
    #     # sample poses from this file 
    #     subsample_indices = np.random.randint(0, len(amass_pose_data['pose']), self.num_pts)
    #     amass_poses = amass_pose_data['pose'][subsample_indices]
    #     if self.flip:
    #         amass_poses, _  = quat_flip(poses)

    #     # assert not np.any(np.isnan(poses))
    #     # assert not np.any(np.isnan(dist))
        

    #     return {'pose': np.array(poses, dtype=np.float32),
    #             'dist': np.array(dist, dtype=np.float32),
    #             'man_poses': np.array(amass_poses, dtype=np.float32),
    #             #'nn_pose': np.array(nn_pose, dtype=np.float32),
    #             #'betas': np.array(betas, dtype=np.float32)
    #             }

    def get_loader(self, shuffle =True):

        return torch.utils.data.DataLoader(
                self, batch_size=self.batch_size, num_workers=self.num_workers, shuffle=shuffle,
                worker_init_fn=self.worker_init_fn, drop_last=True)

    def worker_init_fn(self, worker_id):
        random_data = os.urandom(4)
        base_seed = int.from_bytes(random_data, byteorder="big")
        np.random.seed(base_seed + worker_id)



class PoseData_online(Dataset):
    """not used"""


    def __init__(self, mode, data_path,batch_size=1024, num_workers=3, sigma=0.01):

        self.mode= mode
        self.path = data_path
        self.sigma = sigma
        self.batch_size = batch_size
        self.num_workers = num_workers
        self.load_datasets()

    def load_datasets(self):
        pose_arr = []
        for dset_name in amass_splits[self.mode][:1]:
            npz_fnames = sorted(os.listdir(os.path.join(self.path, dset_name)))
            for npz_fname in npz_fnames[:1]:
                pose_data = np.load(os.path.join(self.path,dset_name, npz_fname))['pose_body']
                pose_arr.extend(axis_angle_to_quaternion(torch.from_numpy(pose_data.reshape(len(pose_data), 21,3)).unsqueeze(0)))
        self.pose = torch.cat(pose_arr)

    def __len__(self):
        return len(self.pose)

    def __getitem__(self, idx):


        poses = self.pose[idx] + self.sigma*np.random.rand(21,4)

        betas = np.zeros(10)


        return {'pose': np.array(poses, dtype=np.float32),
                'pose_id': idx,
                'betas': np.array(betas, dtype=np.float32)}

    def get_loader(self, shuffle =True):

        return torch.utils.data.DataLoader(
                self, batch_size=self.batch_size, num_workers=self.num_workers, shuffle=shuffle,
                worker_init_fn=self.worker_init_fn, drop_last=True)

    def worker_init_fn(self, worker_id):
        random_data = os.urandom(4)
        base_seed = int.from_bytes(random_data, byteorder="big")
        np.random.seed(base_seed + worker_id)

