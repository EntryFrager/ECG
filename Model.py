import torch
import torch.nn as nn
import numpy as np
import os
import warnings 
import random
from sklearn.metrics import roc_auc_score, confusion_matrix, classification_report

warnings.filterwarnings("ignore")
DEFAULT_RANDOM_SEED = 42


def SeedBasic(seed = DEFAULT_RANDOM_SEED):
    random.seed(seed)
    os.environ['PYTHONHASHSEED'] = str(seed)
    np.random.seed(seed)


def SeedTorch(seed = DEFAULT_RANDOM_SEED):
    torch.manual_seed(seed)
    torch.cuda.manual_seed_all(seed)

    torch.backends.cudnn.deterministic = True
    torch.backends.cudnn.benchmark     = False


def SeedEverything(seed = DEFAULT_RANDOM_SEED):
    SeedBasic(seed)
    SeedTorch(seed)


device = 'cuda' if torch.cuda.is_available() else 'cpu'
print(f"Training will take on {device}")

SeedEverything()


class Bottleneck(nn.Module):
    expansion = 4
    
    def __init__(self, inplanes, planes, stride=1, downsample = None):
        super().__init__()

        self.conv_1 = nn.Conv1d(inplanes, planes, kernel_size=1, stride=1, padding=0)
        self.batch_norm_1 = nn.BatchNorm1d(planes)
        self.conv_2 = nn.Conv1d(planes, planes, kernel_size=3, stride=stride, padding=1)
        self.batch_norm_2 = nn.BatchNorm1d(planes)
        self.conv_3 = nn.Conv1d(planes, planes * self.expansion, kernel_size=1, stride=1, padding=0)
        self.batch_norm_3 = nn.BatchNorm1d(planes * self.expansion)

        self.relu = nn.ReLU()
        self.downsample = downsample
        self.stride = stride

    
    def forward(self, x):
        residual = x

        out = self.conv_1(x)
        out = self.batch_norm_1(out)
        out = self.relu(out)

        out = self.conv_2(out)
        out = self.batch_norm_2(out)
        out = self.relu(out)

        out = self.conv_3(out)
        out = self.batch_norm_3(out)
        out = self.relu(out)

        if self.downsample is not None:
            residual = self.downsample(x)

        out = out + residual
        out = self.relu(out)

        return out


class ResNet(nn.Module):
    def __init__(self, block, layers, num_classes=1000):
        super().__init__()

        self.inplanes = 64

        self.conv_1 = nn.Conv1d(12, 64, kernel_size=15, stride=2, padding=7, bias=False)
        self.batch_norm_1 = nn.BatchNorm1d(64)
        self.relu = nn.ReLU()
        self.maxpool_1 = nn.MaxPool1d(kernel_size=3, stride=2, padding=1)
        
        self.layer_1 = self._make_layer(block, 64, layers[0])
        self.layer_2 = self._make_layer(block, 128, layers[1], stride=2)
        self.layer_3 = self._make_layer(block, 256, layers[2], stride=2)
        self.layer_4 = self._make_layer(block, 512, layers[3], stride=2)

        self.avg_pool = nn.AdaptiveAvgPool1d(1)
        self.fc = nn.Linear(512 * block.expansion, num_classes)

        for m in self.modules():
            if isinstance(m, nn.Conv1d):
                nn.init.kaiming_normal_(m.weight, mode='fan_out', nonlinearity='relu')
            elif isinstance(m, nn.BatchNorm1d):
                nn.init.constant_(m.weight, 1)
                nn.init.constant_(m.bias, 0)


    def forward(self, x):
        out = self.conv_1(x)
        out = self.batch_norm_1(out)
        out = self.relu(out)
        out = self.maxpool_1(out)

        out = self.layer_1(out)
        out = self.layer_2(out)
        out = self.layer_3(out)
        out = self.layer_4(out)

        out = self.avg_pool(out)
        out = torch.flatten(out, 1)
        out = self.fc(out)

        return out


    def _make_layer(self, block, planes, blocks, stride=1):
        downsample = None
        if stride != 1 or self.inplanes != planes * block.expansion:
            downsample = nn.Sequential(
                nn.Conv1d(self.inplanes, planes * block.expansion, kernel_size=1, stride=stride, bias=False),
                nn.BatchNorm1d(planes * block.expansion)
            )

        layers = []
        layers.append(block(self.inplanes, planes, stride, downsample))
        self.inplanes = planes * block.expansion
        for _ in range(1, blocks):
            layers.append(block(self.inplanes, planes))

        return nn.Sequential(*layers)


def train(net, train_loader, val_loader, 
          n_epoch, optimizer, criterion, treshold_preds):
    loss_train_history = []
    loss_val_history   = []

    for epoch in range(n_epoch):
        print('Epoch {}/{}:'.format(epoch + 1, n_epoch), flush = True)

        train_loss = val_loss = 0.0
        train_labels, train_preds, train_prob, val_labels, val_preds, val_prob = [], [], [], [], [], []

        net.train()

        for (batch_idx, train_batch) in enumerate(train_loader):
            samples, labels = train_batch['ecg_signals'].to(device), train_batch['labels'].to(device)
            optimizer.zero_grad()

            preds = net(samples)
            loss = criterion(preds, labels)

            loss.backward()
            optimizer.step()

            train_loss += loss.item()

            with torch.no_grad():
                preds = torch.sigmoid(preds)
                bin_preds = (preds > treshold_preds).float()
                
                train_prob.append(preds.cpu().numpy())
                train_labels.append(labels.cpu().numpy())
                train_preds.append(bin_preds.cpu().numpy())
        
        train_loss /= len(train_loader)
        loss_train_history.append(train_loss)

        net.eval()

        with torch.no_grad():
            for val_batch in val_loader:
                samples, labels = val_batch['ecg_signals'].to(device), val_batch['labels'].to(device)
                preds = net(samples)
                val_loss += criterion(preds, labels).item()

                preds = torch.sigmoid(preds)

                bin_preds = (preds > treshold_preds).float()

                val_prob.append(preds.cpu().numpy())
                val_labels.append(labels.cpu().numpy())
                val_preds.append(bin_preds.cpu().numpy())

        val_loss /= len(val_loader)
        loss_val_history.append(val_loss)

        print('Validation metrics:')
        metric_func(np.concatenate(val_labels), np.concatenate(val_preds), np.concatenate(val_prob))

        print(f'train Loss: {train_loss:.4f}\n'
              f'val Loss: {val_loss:.4f}')
    
    return net, loss_train_history, loss_val_history


def test(net, test_loader, criterion, treshold_preds):
    net.eval()

    test_loss = 0.0
    test_preds, test_labels, test_prob = [], [], []

    with torch.no_grad():
        for (batch_idx, test_batch) in enumerate(test_loader): 
            samples, labels = test_batch['ecg_signals'].to(device), test_batch['labels'].to(device)
            preds = net(samples)

            test_loss += criterion(preds, labels).item()

            preds = torch.sigmoid(preds)

            bin_preds = (preds > treshold_preds).float()
            test_prob.append(preds.cpu().numpy())
            test_labels.append(labels.cpu().numpy())
            test_preds.append(bin_preds.cpu().numpy())

    test_loss /= len(test_loader)

    print('Test metrics:')
    metric_func(np.concatenate(test_labels), np.concatenate(test_preds), np.concatenate(test_prob))

    print(f'test Loss: {test_loss:.4f}')

    return test_loss


def metric_func(bin_labels, bin_preds, preds):    
    TP, FP, TN, FN = [], [], [], []
    sensitivity, specificity, precision = [], [], []
    my_f1   = []
    roc_auc = []

    for i in range(0, bin_labels.shape[1]):
        conf_matrix = confusion_matrix(bin_labels[:, i], bin_preds[:, i], labels=[0, 1])
        TP_i, FP_i, TN_i, FN_i = conf_matrix[1][1], conf_matrix[0][1], conf_matrix[0][0], conf_matrix[1][0]

        print('\t\tTP\tFP\tTN\tFN')
        print(f'\t\t{TP_i}\t{FP_i}\t{TN_i}\t{FN_i}')
        
        sensitivity_i = TP_i / (TP_i + FN_i)
        specificity_i = TN_i / (TN_i + FP_i)
        precision_i   = TP_i / (TP_i + FP_i)

        TP.append(TP_i)
        FP.append(FP_i)
        TN.append(TN_i)
        FN.append(FN_i)

        sensitivity.append(sensitivity_i)
        specificity.append(specificity_i)
        precision.append(precision_i)

        my_f1.append(2 * sensitivity_i * precision_i / (sensitivity_i + precision_i))
        roc_auc.append(roc_auc_score(bin_labels[:, i], preds[:, i]))

    # micro averaging

    TP_mean, FP_mean, TN_mean, FN_mean = np.mean(TP), np.mean(FP), np.mean(TN), np.mean(FN)
    micro_sensitivity = TP_mean / (TP_mean + FN_mean)
    micro_specificity = TN_mean / (TN_mean + FP_mean)
    micro_precision   = TP_mean / (TP_mean + FP_mean)

    micro_f1 = 2 * micro_sensitivity * micro_precision / (micro_sensitivity + micro_precision)

    print(f'Micro averaging:'                           \
          f'\n\tsensitivity: {micro_sensitivity:.4f}'   \
          f'\n\tspecificity: {micro_specificity:.4f}'   \
          f'\n\tprecision:   {micro_precision:.4f}'     \
          f'\n\tf1 score:    {micro_f1:.4f}')

    # macro averaging

    macro_sensitivity = np.mean(sensitivity)
    macro_specificity = np.mean(specificity)
    macro_precision = np.mean(precision)

    macro_f1 = 2 * macro_sensitivity * macro_precision / (macro_sensitivity + macro_precision)

    print(f'Macro averaging:'                           \
          f'\n\tsensitivity: {macro_sensitivity:.4f}'   \
          f'\n\tspecificity: {macro_specificity:.4f}'   \
          f'\n\tprecision:   {macro_precision:.4f}'     \
          f'\n\tf1 score:    {macro_f1:.4f}')

    # weighted averaging
    
    print(f'Weighted averaging with w_k=1:'             \
          f'\n\tf1_score:   {np.mean(my_f1)}')

    # roc auc and classification report

    print(f'ROC AUC: {np.mean(roc_auc)}')

    print(f'Classification report:\n{classification_report(bin_labels, bin_preds)}')

