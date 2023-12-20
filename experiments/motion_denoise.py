import argparse
from configs.config import load_config
# General config

#from model_quat import  train_manifold2 as train_manifold
from model.posendf import PoseNDF
import shutil
from data.data_splits import amass_splits
import ipdb
import torch
import numpy as np
from body_model import BodyModel
from exp_utils import renderer

from pytorch3d.transforms import axis_angle_to_quaternion, axis_angle_to_matrix, matrix_to_quaternion
from tqdm import tqdm
from pytorch3d.io import save_obj
import os

class MotionDenoise(object):
    def __init__(self, posendf,  body_model, out_path='./experiment_results/motion_denoise', debug=False, device='cpu', batch_size=1, gender='male'):
        self.debug = debug
        self.device = device
        self.pose_prior = posendf
        self.body_model = body_model
        self.out_path = out_path
        self.betas = torch.zeros((batch_size,10)).to(device=self.device)
    
    def get_loss_weights(self):
        """Set loss weights"""
        loss_weight = {'temp': lambda cst, it: 10. ** 1 * cst * (1 + it),
                       'data': lambda cst, it: 10. ** 2 * cst / (1 + it),
                       'pose_pr': lambda cst, it:  10. ** 7 * cst * cst / (1 + it)
                       }
        return loss_weight

    @staticmethod
    def backward_step(loss_dict, weight_dict, it):
        w_loss = dict()
        for k in loss_dict:
            w_loss[k] = weight_dict[k](loss_dict[k], it)

        tot_loss = list(w_loss.values())
        tot_loss = torch.stack(tot_loss).sum()
        return tot_loss

    @staticmethod
    def visualize(vertices, faces, out_path, device, joints=None, render=False, prefix='out', save_mesh=False):
        # save meshes and rendered results if needed
        os.makedirs(out_path,exist_ok=True)
        if save_mesh:
            os.makedirs( os.path.join(out_path, 'meshes'), exist_ok=True)
            [save_obj(os.path.join(out_path, 'meshes', '{}_{:04}.obj'.format(prefix,i) ), vertices[i], faces) for i in range(len(vertices))]

        if render:
            renderer(vertices, faces, out_path, device=device,  prefix=prefix)
        
    def optimize(self, noisy_poses, gt_poses=None,  iterations=10, steps_per_iter=50):
        # create initial SMPL joints and vertices for visualition(to be used for data term)
        smpl_init = self.body_model(betas=self.betas, pose_body=noisy_poses.view(-1, 69)) 
        self.visualize(smpl_init.vertices, smpl_init.faces, self.out_path, device=self.device, joints=smpl_init.Jtr, render=True, prefix='init')

        init_joints = torch.from_numpy(smpl_init.Jtr.detach().cpu().numpy().astype(np.float32)).to(device=self.device)
        init_verts = torch.from_numpy(smpl_init.vertices.detach().cpu().numpy().astype(np.float32)).to(device=self.device)
        init_pose = noisy_poses.detach().cpu().numpy()
        # Optimizer
        smpl_init.body_pose.requires_grad= True
        self.betas.requires_grad = False
        
        optimizer = torch.optim.Adam([smpl_init.body_pose], 0.02, betas=(0.9, 0.999))
        # Get loss_weights
        weight_dict = self.get_loss_weights()

        for it in range(iterations):
            loop = tqdm(range(steps_per_iter))
            loop.set_description('Optimizing SMPL poses')
            for i in loop:
                optimizer.zero_grad()
                loss_dict = dict()
                # convert pose to quaternion and  predict distance
                pose_quat = axis_angle_to_quaternion(smpl_init.body_pose.view(-1, 23, 3)[:, :21])
                dis_val = self.pose_prior(pose_quat, train=False)['dist_pred']
                loss_dict['pose_pr']= torch.mean(dis_val)

                # calculate temporal loss between mesh vertices
                smpl_init = self.body_model(betas=self.betas, pose_body=smpl_init.body_pose)
                # smpl_opt = self.smpl(betas=self.betas, pose_body=smpl_init.body_pose)
                temp_term = smpl_init.vertices[:-1] - smpl_init.vertices[1:]
                loss_dict['temp'] = torch.mean(torch.sqrt(torch.sum(temp_term*temp_term, dim=2)))

                # calculate data term from inital noisy pose
                if it > 0: #for nans
                    data_term = smpl_init.Jtr  - init_joints
                    loss_dict['data'] = torch.mean(torch.sqrt(torch.sum(data_term*data_term, dim=2)))   

                # Get total loss for backward pass
                tot_loss = self.backward_step(loss_dict, weight_dict, it)
                tot_loss.backward()
                optimizer.step()


                l_str = 'Iter: {}'.format(i)
                for k in loss_dict:
                    l_str += ', {}: {:0.8f}'.format(k, weight_dict[k](loss_dict[k], it).mean().item())
                    loop.set_description(l_str)


        # ipdb.set_trace()
        # create final results
        smpl_init = self.body_model(betas=self.betas, pose_body=smpl_init.body_pose)
        v2v_error = smpl_init.vertices - init_verts
        self.visualize(smpl_init.vertices, smpl_init.faces, self.out_path, device=self.device, joints=smpl_init.Jtr, render=True,prefix='out')

        if gt_poses is not None:
            smpl_gt = self.body_model(betas=self.betas, pose_body=gt_poses)
            self.visualize(smpl_gt.vertices, smpl_gt.faces, self.out_path, device=self.device, joints=smpl_init.Jtr, render=True,prefix='gt')

            v2v_error = smpl_init.vertices - smpl_gt.vertices
        v2v_error = torch.mean(torch.sqrt(torch.sum(v2v_error*v2v_error, dim=2)))*100.
        print('V2V from noisy input:{:0.8f} cm'.format(v2v_error))
        return v2v_error.detach().cpu().numpy()

def main(opt, ckpt, motion_file,gt_data=None, out_path=None):
    ### load the model
    net = PoseNDF(opt)
    device= 'cpu'
    ckpt = torch.load(ckpt, map_location='cpu')['model_state_dict']
    net.load_state_dict(ckpt)
    net.eval()
    net = net.to(device)


    motion_data = np.load(motion_file)['pose_body'][10000:10500,:63]
    batch_size = len(motion_data)
    pose_body = torch.from_numpy(motion_data.astype(np.float32)).to(device=device)
    noisy_poses = torch.zeros((batch_size, 69)).to(device=device)
    noisy_poses[:, :63] = pose_body

    #  load body model
    bm_dir_path = 'C:/Users/thiba/Desktop/Semester_project/PoseNDF/SMPL_python_v.1.1.0/smpl'
    body_model = BodyModel(bm_path=bm_dir_path, model_type='smpl', batch_size=batch_size,  num_betas=10).to(device=device)

    if gt_data is not None:

        motion_data_gt = np.load(motion_file)['pose_body'][10000:10500,:63]
        batch_size = len(motion_data_gt)
        pose_body = torch.from_numpy(motion_data_gt.astype(np.float32)).to(device=device)
        gt_poses = torch.zeros((batch_size, 69)).to(device=device)
        gt_poses[:, :63] = pose_body
    # create Motion denoiser layer
    motion_denoiser = MotionDenoise(net, body_model=body_model, batch_size=len(noisy_poses), out_path=out_path)
    v2v_err= motion_denoiser.optimize(noisy_poses, gt_poses)
    return v2v_err




if __name__ == '__main__':
    parser = argparse.ArgumentParser(
        description='Interpolate using PoseNDF.'
    )
    parser.add_argument('--config', '-c', default='configs/posendf.yaml', type=str, help='Path to config file.')
    parser.add_argument('--ckpt_path', '-ckpt', default='C:/Users/thiba/Desktop/Semester_project/PoseNDF/results/trained_model/main_lrelu_l2_1e-05_dist1.0_eik1.0/checkpoints/checkpoint_epoch_best.tar', type=str, help='Path to pretrained model.')
    parser.add_argument('--motion_data', '-mf', default='C:/Users/thiba/Desktop/Semester_project/PoseNDF/data/OH0037_body/pose3d_np_body.npz', type=str, help='Path to noisy motion file')
    parser.add_argument('--outpath_folder', '-out', default='C:/Users/thiba/Desktop/Semester_project/PoseNDF/results/motion_denoise_results/', type=str, help='Path to output')
    parser.add_argument('--gt_data', '-gt', default='C:/Users/thiba/Desktop/Semester_project/PoseNDF/data/OH0037_body/pose3d_np_body.npz', type=str, help='Path to ground truth motion file')
    args = parser.parse_args()

    opt = load_config(args.config)

    #running for tables:
    # datas = ['amass_noise_0.01_60', 'amass_noise_0.05_60', 'amass_noise_0.1_60', 'amass_noise_0.5_60', 'amass_noise_0.1_120', 'amass_noise_0.5_120' , 'amass_noise_0.1_240']
    # datas = ['amass_noise_0.5_60', 'amass_noise_0.01_60', 'amass_noise_0.05_60', 'amass_noise_0.1_60']
    all_results = {}
    # for data in datas:
        # data_dir = '/BS/humanpose/static00/experiments/humor/results/out/{}/results_out'.format(data)
        # args.outpath_folder = '/BS/humanpose/static00/experiments/humor/results/posendf/{}'.format(data)
    os.makedirs(args.outpath_folder ,exist_ok=True)
    # seqs = sorted(os.listdir(data_dir))
    all_error = []
        # for seq in seqs:
            # out_path = os.path.join(args.outpath_folder, seq)
    os.makedirs(args.outpath_folder,exist_ok=True)
            # obs_path =  os.path.join(data_dir, seq, 'observations.npz')
    if os.path.exists(args.ckpt_path):
        v2v_err = main(opt, args.ckpt_path,args.motion_data, gt_data= args.gt_data,out_path=args.outpath_folder)
        all_error.append(v2v_err)
    all_results=  np.array(all_error)
    #ipdb.set_trace()
    print(all_results)
    out_path = os.path.join(args.outpath_folder, "motion_denoise_results.npz")
    np.savez(out_path, results=all_results)
