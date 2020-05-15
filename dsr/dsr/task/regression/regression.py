import numpy as np

from .dataset import Dataset


def make_regression_task(name, metric, metric_params, dataset, threshold=1e-12):
    """
    Factory function for regression rewards. This includes closures for a
    dataset and regression metric (e.g. inverse NRMSE). Also sets regression-
    specific metrics to be used by Programs.

    Parameters
    ----------
   
    metric : str
        Name of reward function metric to use.

    metric_params : list
        List of metric-specific parameters.

    dataset : dict
        Dict of .dataset.Dataset kwargs.

    Returns
    -------

    See dsr.task.task.make_task().
    """
    
    # Define closures for dataset and metric
    dataset["name"] = name # TBD: Refactor to not have two instances of "name"
    dataset = Dataset(**dataset)
    X_train = dataset.X_train
    y_train = dataset.y_train
    X_test = dataset.X_test
    y_test = dataset.y_test
    y_train_noiseless = dataset.y_train_noiseless
    y_test_noiseless = dataset.y_test_noiseless
    var_y_test = np.var(dataset.y_test) # Save time by only computing this once
    var_y_test_noiseless = np.var(dataset.y_test_noiseless) # Save time by only computing this once
    metric = make_regression_metric(metric, y_train, *metric_params)


    def reward(p):

        # Compute estimated values
        y_hat = p.execute(X_train)

        # Return metric
        r = metric(y_train, y_hat)
        return r


    def evaluate(p):

        # Compute predictions on test data
        y_hat = p.execute(X_test)

        # NMSE on test data (used to report final error)
        nmse_test = np.mean((y_test - y_hat)**2) / var_y_test

        # NMSE on noiseless test data (used to determine recovery)
        nmse_test_noiseless = np.mean((y_test_noiseless - y_hat)**2) / var_y_test_noiseless

        # Success is defined by NMSE on noiseless test data below a threshold
        success = nmse_test_noiseless < threshold

        info = {
            "nmse_test" : nmse_test,
            "nmse_test_noiseless" : nmse_test_noiseless,
            "success" : success
        }
        return info

    stochastic = False # Regression rewards are deterministic


    return reward, evaluate, dataset.function_set, dataset.n_input_var, stochastic


def make_regression_metric(name, y_train, *args):
    """
    Factory function for a regression metric. This includes a closures for
    metric parameters and the variance of the training data.

    Parameters
    ----------

    name : str
        Name of metric. See all_metrics for supported metrics.

    args : args
        Metric-specific parameters

    Returns
    -------

    metric : function
        Regression metric mapping true and estimated values to a scalar.
    """

    if "nmse" in name or "nrmse" in name:
        var_y = np.var(y_train)

    all_metrics = {

        # Negative mean squared error
        # Range: [-inf, 0]
        # Value = -var(y) when y_hat == mean(y)
        "neg_mse" :     (lambda y, y_hat : -np.mean((y - y_hat)**2),
                        0),

        # Negative normalized mean squared error
        # Range: [-inf, 0]
        # Value = -1 when y_hat == mean(y)
        "neg_nmse" :    (lambda y, y_hat : -np.mean((y - y_hat)**2)/var_y,
                        0),

        # Negative normalized root mean squared error
        # Range: [-inf, 0]
        # Value = -1 when y_hat == mean(y)
        "neg_nrmse" :   (lambda y, y_hat : -np.sqrt(np.mean((y - y_hat)**2)/var_y),
                        0),

        # (Protected) inverse mean squared error
        # Range: [0, 1]
        # Value = 1/(1 + var(y)) when y_hat == mean(y)
        "inv_mse" : (lambda y, y_hat : 1/(1 + np.mean((y - y_hat)**2)),
                        0),

        # (Protected) inverse normalized mean squared error
        # Range: [0, 1]
        # Value = 0.5 when y_hat == mean(y)
        "inv_nmse" :    (lambda y, y_hat : 1/(1 + np.mean((y - y_hat)**2)/var_y),
                        0),

        # (Protected) inverse normalized root mean squared error
        # Range: [0, 1]
        # Value = 0.5 when y_hat == mean(y)
        "inv_nrmse" :    (lambda y, y_hat : 1/(1 + np.sqrt(np.mean((y - y_hat)**2)/var_y)),
                        0),

        # Fraction of predicted points within p0*abs(y) + p1 band of the true value
        # Range: [0, 1]
        "fraction" :    (lambda y, y_hat : np.mean(abs(y - y_hat) < args[0]*abs(y) + args[1]),
                        2),

        # Pearson correlation coefficient
        # Range: [0, 1]
        "pearson" :     (lambda y, y_hat : scipy.stats.pearsonr(y, y_hat)[0],
                        0),

        # Spearman correlation coefficient
        # Range: [0, 1]
        "spearman" :    (lambda y, y_hat : scipy.stats.spearmanr(y, y_hat)[0],
                        0)
    }

    assert name in all_metrics, "Unrecognized reward function name."
    assert len(args) == all_metrics[name][1], "Expected {} reward function parameters; received {}.".format(all_metrics[name][1], len(args))
    metric = all_metrics[name][0]
    return metric