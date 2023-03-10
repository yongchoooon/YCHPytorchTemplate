import argparse
import collections
import torch
import numpy as np
import data_loader.data_loaders as module_data
import model.loss as module_loss
import model.metric as module_metric
import model.model as module_arch
from slack_alarm import SlackSender
from parse_config import ConfigParser
from trainer import Trainer
from utils import prepare_device
from custom_lr_scheduler import CosineAnnealingWarmUpRestarts as CAWUR


# fix random seeds for reproducibility
SEED = 123
torch.manual_seed(SEED)
torch.backends.cudnn.deterministic = True
torch.backends.cudnn.benchmark = False
np.random.seed(SEED)

def main(config):
    try:
        logger = config.get_logger('train')

        # setup data_loader instances
        data_loader = config.init_obj('data_loader', module_data)
        # valid_data_loader = data_loader.split_validation()
        valid_data_loader = config.init_obj('data_loader', module_data, validation = True)

        # build model architecture, then print to console
        model = config.init_obj('arch', module_arch)
        logger.info(model)

        # prepare for (multi-device) GPU training
        device, device_ids = prepare_device(config['n_gpu'])
        model = model.to(device)
        if len(device_ids) > 1:
            model = torch.nn.DataParallel(model, device_ids=device_ids)

        # get function handles of loss and metrics
        criterion = getattr(module_loss, config['loss'])
        metrics = [getattr(module_metric, met) for met in config['metrics']]

        # build optimizer, learning rate scheduler. delete every lines containing lr_scheduler for disabling scheduler
        trainable_params = filter(lambda p: p.requires_grad, model.parameters())
        optimizer = config.init_obj('optimizer', torch.optim, trainable_params)

        ### custom optimizer ###
        # bias_parameters = []
        # weight_parameters = []
        # for name, param in model.named_parameters():
        #     if 'bias' in name:
        #         bias_parameters.append(param)
        #     else:
        #         weight_parameters.append(param)
        # optimizer = torch.optim.SGD([
        #     {'params': weight_parameters, 'weight_decay': 0.0001},
        #     {'params': bias_parameters, 'weight_decay': 0}
        #     ], lr=0.1, momentum=0.9, nesterov=True)
        ### custom optimizer ###

        if config['lr_scheduler']['type'] == 'CosineAnnealingWarmUpRestarts':
            lr_scheduler = CAWUR(optimizer, **config['lr_scheduler']['args'])
        if config['lr_scheduler']['type'] == 'LambdaLR':
            def func(epoch):
                lr = config['optimizer']['args']['lr']
                if epoch <= 100:
                    return lr
                elif epoch > 100 and epoch <= 200 and epoch % 10 == 0:
                    return lr * (0.5 ** ((epoch - 90) // 10)) 
                else: 
                    return lr * (0.5 ** ((epoch - 90) // 10))
            lr_scheduler = config.init_obj('lr_scheduler', torch.optim.lr_scheduler, optimizer, lr_lambda=func)
        else:
            lr_scheduler = config.init_obj('lr_scheduler', torch.optim.lr_scheduler, optimizer)

        trainer = Trainer(model, criterion, metrics, optimizer,
                        config=config,
                        device=device,
                        data_loader=data_loader,
                        valid_data_loader=valid_data_loader,
                        lr_scheduler=lr_scheduler)

        trainer.train()
        
    except Exception as ex:
        SlackSender(config).slack_error_sender(ex)


if __name__ == '__main__':
    args = argparse.ArgumentParser(description='PyTorch Template')
    args.add_argument('-c', '--config', default=None, type=str,
                      help='config file path (default: None)')
    args.add_argument('-r', '--resume', default=None, type=str,
                      help='path to latest checkpoint (default: None)')
    args.add_argument('-d', '--device', default=None, type=str,
                      help='indices of GPUs to enable (default: all)')

    # custom cli options to modify configuration from default values given in json file.
    CustomArgs = collections.namedtuple('CustomArgs', 'flags type target')
    options = [
        CustomArgs(['--lr', '--learning_rate'], type=float, target='optimizer;args;lr'),
        CustomArgs(['--bs', '--batch_size'], type=int, target='data_loader;args;batch_size')
    ]
    config = ConfigParser.from_args(args, options)
    main(config)
