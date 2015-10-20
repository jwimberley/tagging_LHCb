import numpy
import pandas
from collections import OrderedDict

from sklearn import clone
from sklearn.metrics import roc_auc_score
from sklearn.linear_model import LogisticRegression
from sklearn.isotonic import IsotonicRegression

from matplotlib import pyplot as plt
from rep.utils import train_test_split, train_test_split_group, Flattener
from scipy.special import logit, expit


def union(*arrays):
    return numpy.concatenate(arrays)


def get_events_statistics(data, id_column='event_id'):
    """
    :return: dict with 'Events' - number of events and 'tracks' - number of samples
    """
    return {'Events': len(numpy.unique(data[id_column])), 'tracks': len(data)}


def get_events_number(data, id_column='event_id'):
    """
    :return: number of B events
    """
    _, data_ids = numpy.unique(data[id_column], return_inverse=True)
    weights = numpy.bincount(data_ids, weights=data.N_sig_sw) / numpy.bincount(data_ids)
    return numpy.sum(weights)

def get_N_B_events():
    '''
    :return: number of B decays (sum of sWeight in initial root file) 
    '''
    N_B_decays = 7.42867714256286621e+05
    return N_B_decays

    
def plot_flattened_probs(probs, labels, weights, label=1, check_input=True):
    """
    Prepares transformation, which turns predicted probabilities to uniform in [0, 1] distribution.
    
    :param probs: probabilities, numpy.array of shape [n_samples, 2]
    :param labels: numpy.array of shape [n_samples] with labels (0 and 1)
    :param weights: numpy.array of shape [n_samples]
    :param label: int, predictions of this class will be turned to uniform.
    
    :return: flattener
    """
    if check_input:
        probs, labels, weights = numpy.array(probs), numpy.array(labels), numpy.array(weights)
        assert probs.shape[1] == 2
        assert numpy.in1d(labels, [0, 1]).all()
    
    signal_probs = probs[:, 1]
    flattener = Flattener(signal_probs[labels == label], weights[labels == label])
    flat_probs = flattener(signal_probs)
    
    plt.hist(flat_probs[labels == 1], bins=100, normed=True, histtype='step', 
             weights=weights[labels == 1], label='same sign')
    plt.hist(flat_probs[labels == 0], bins=100, normed=True, histtype='step', 
             weights=weights[labels == 0], label='opposite sign')
    plt.xlabel('predictions')
    plt.legend(loc='upper center')
    plt.show()
    return flattener


def bootstrap_calibrate_prob(labels, weights, probs, n_calibrations=30, group_column=None, threshold=0.):
    """
    Bootstrap isotonic calibration: 
     * randomly divide data into train-test
     * on train isotonic is fitted and applyed to test
     * on test using calibrated probs p(B+) D2 and auc are calculated 
    
    :param probs: probabilities, numpy.array of shape [n_samples]
    :param labels: numpy.array of shape [n_samples] with labels 
    :param weights: numpy.array of shape [n_samples]
    :param threshold: float, to set labels 0/1 
    
    :return: D2 array and auc array
    """
    aucs = []
    D2_array = []
    labels = (labels > threshold) * 1
    
    for _ in range(n_calibrations):
        if group_column is not None:
            train_probs, test_probs, train_labels, test_labels, train_weights, test_weights = train_test_split_group(
                group_column, probs, labels, weights, train_size=0.5)
        else:
            train_probs, test_probs, train_labels, test_labels, train_weights, test_weights = train_test_split(
                probs, labels, weights, train_size=0.5)
        iso_est = IsotonicRegression(y_min=0, y_max=1, out_of_bounds='clip')
        iso_est.fit(train_probs, train_labels, train_weights)
        probs_calib = iso_est.transform(test_probs)
        alpha = (1 - 2 * probs_calib) ** 2
        aucs.append(roc_auc_score(test_labels, test_probs, sample_weight=test_weights))
        D2_array.append(numpy.average(alpha, weights=test_weights))
    return D2_array, aucs


def predict_by_estimator(estimator, datasets):
    '''
    Predict data by classifier
    Important note: this also works correctly if classifier is FoldingClassifier and one of dataframes is his training data.
    
    :param estimator: REP classifier, already trained model.
    :param datasets: list of pandas.DataFrames to predict.
        
    :return: data, probabilities
    '''     
    data = pandas.concat(datasets)    
    # predicting each DataFrame separately to preserve FoldingClassifier
    probs = numpy.concatenate([estimator.predict_proba(dataset)[:, 1] for dataset in datasets])
    return data, probs


def result_table(tagging_efficiency, tagging_efficiency_delta, D2, auc, name='model'):
    """
    Represents results of tagging in a nice table.
    
    :param tagging_efficiency: float, which part of samples will be tagged
    :param tagging_efficiency_delta: standard error of efficiency
    :param D2: D^2, average value ((p(B+) - 0.5)*2)^2 for sample
    :param name: str, name of model
    :param auc: full auc, calculated with untag events (probs are set 0.5) with B+/B- labels
    
    :return: pandas.DataFrame with only one row, describing result_table
    
    Use pandas.concat to get table with results of different methods.
    """
    result = OrderedDict()
    result['name'] = name
    result['$\epsilon_{tag}, \%$'] = [tagging_efficiency * 100.]
    result['$\Delta \epsilon_{tag}, \%$'] = [tagging_efficiency_delta * 100.]
    result['$D^2$'] = [numpy.mean(D2)]
    result['$\Delta D^2$'] = [numpy.std(D2)]
    epsilon = numpy.mean(D2) * tagging_efficiency * 100.
    result['$\epsilon, \%$'] = [epsilon]
    relative_D2_error = numpy.std(D2) / numpy.mean(D2)
    relative_eff_error = efficiency_delta / tagging_efficiency
    relative_epsilon_error = numpy.sqrt(relative_D2_error ** 2 + relative_eff_error ** 2) 
    result['$\Delta \epsilon, \%$'] = [relative_epsilon_error * epsilon]
    result['AUC, with untag'] = [numpy.mean(auc) * 100]
    result['$\Delta$ AUC, with untag'] = [numpy.std(auc) * 100]
    return pandas.DataFrame(result)


def calibrate_probs(labels, weights, probs, logistic=False, random_state=11, threshold=0.):
    """
    Calibrate output to probabilities using 2-folding to calibrate all data
    
    :param probs: probabilities, numpy.array of shape [n_samples]
    :param labels: numpy.array of shape [n_samples] with labels 
    :param weights: numpy.array of shape [n_samples]
    :param threshold: float, to set labels 0/1 
    :param logistic: bool, use logistic or isotonic regression

    :return: calibrated probabilities
    """
    labels = (labels > threshold) * 1
    ind = numpy.arange(len(probs))
    ind_1, ind_2 = train_test_split(ind, random_state=random_state, train_size=0.5)
    
    calibrator = LogisticRegression(C=100) if logistic else IsotonicRegression(y_min=0, y_max=1, out_of_bounds='clip')
    est_calib_1, est_calib_2 = clone(calibrator), clone(calibrator)
    probs_1 = probs[ind_1]
    probs_2 = probs[ind_2]
    
    if logistic:
        probs_1 = logit(probs_1)[:, numpy.newaxis]
        probs_2 = logit(probs_2)[:, numpy.newaxis]
        est_calib_1.fit(probs_1, labels[ind_1])
        est_calib_2.fit(probs_2, labels[ind_2])        
    else:
        est_calib_1.fit(probs_1, labels[ind_1], weights[ind_1])
        est_calib_2.fit(probs_2, labels[ind_2], weights[ind_2])
        
    calibrated_probs = numpy.zeros(len(probs))
    if logistic:
        calibrated_probs[ind_1] = est_calib_2.predict_proba(probs_1)[:, 1]
        calibrated_probs[ind_2] = est_calib_1.predict_proba(probs_2)[:, 1]
    else:
        calibrated_probs[ind_1] = est_calib_2.transform(probs_1)
        calibrated_probs[ind_2] = est_calib_1.transform(probs_2)
    return calibrated_probs


def calculate_auc_with_and_without_untag_events(Bsign, Bprobs, Bweights):
    """
    Calculate AUC score for data and AUC full score for data and untag data (p(B+) for untag data is set to 0.5)
    
    :param Bprobs: p(B+) probabilities, numpy.array of shape [n_samples]
    :param Bsign: numpy.array of shape [n_samples] with labels {-1, 1}
    :param Bweights: numpy.array of shape [n_samples]
    
    :return: auc, full auc
    """
    N_B_not_passed = get_N_B_events() - sum(Bweights)
    Bsign_not_passed = [-1, 1]
    Bprobs_not_passed = [0.5] * 2
    Bweights_not_passed = [N_B_not_passed / 2.] * 2
    
    auc_full = roc_auc_score(union(Bsign, Bsign_not_passed), union(Bprobs, Bprobs_not_passed),
                             sample_weight=union(Bweights, Bweights_not_passed))
    auc = roc_auc_score(Bsign, Bprobs, sample_weight=Bweights)
    return auc, auc_full


def compute_B_prob_using_part_prob(data, probs, weight_column='N_sig_sw', event_id_column='event_id', signB_column='signB',
                                   sign_part_column='signTrack'):
    """
    Compute p(B+) using probs for parts of event (tracks/vertices).
    
    :param data: pandas.DataFrame, data
    :param probs: probabilities for parts of events, numpy.array of shape [n_samples]
    :param weight_column: column for weights in data
    :param event_id_column: column for event id in data
    :param signB_column: column for event B sign in data
    :param sign_part_column: column for part sign in data
    
    :return: B sign array, B weight array, B+ prob array, B event id
    """
    result_event_id, data_ids = numpy.unique(data[event_id_column].values, return_inverse=True)
    log_probs = numpy.log(probs) - numpy.log(1 - probs)
    log_probs *= data[sign_part_column].values
    result_logprob = numpy.bincount(data_ids, weights=log_probs)
    result_label = numpy.bincount(data_ids, weights=data[signB_column].values) / numpy.bincount(data_ids)
    result_weight = numpy.bincount(data_ids, weights=data[weight_column]) / numpy.bincount(data_ids)
    return result_label, result_weight, expit(result_logprob), result_event_id