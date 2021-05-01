import random
import operator
import copy
import warnings
from functools import partial, wraps
from operator import attrgetter
import numpy as np

from dsr.functions import function_map, UNARY_TOKENS, BINARY_TOKENS
from dsr.const import make_const_optimizer
from dsr.program import Program,  _finish_tokens
from dsr.task.regression.dataset import BenchmarkDataset
from dsr import gp_base

try:
    from deap import gp
    from deap import base
    from deap import tools
    from deap import creator
    from deap import algorithms
except ImportError:
    gp          = None
    base        = None
    tools       = None
    creator     = None
    algorithms  = None


### >>> These are components to be removed...

TRIG_TOKENS = ["sin", "cos", "tan", "csc", "sec", "cot"]


# Define inverse tokens
INVERSE_TOKENS = {
    "exp" : "log",
    "neg" : "neg",
    "inv" : "inv",
    "sqrt" : "n2"
}


# Add inverse trig functions
INVERSE_TOKENS.update({
    t : "arc" + t for t in TRIG_TOKENS
    })


# Add reverse
INVERSE_TOKENS.update({
    v : k for k, v in INVERSE_TOKENS.items()
    })


def check_const(ind):
    """Returns True if children of a parent are all const tokens."""

    names = [node.name for node in ind]
    for i, name in enumerate(names):
        if name in UNARY_TOKENS and "const" in names[i+1]:
            return True
        if name in BINARY_TOKENS and "const" in names[i+1] and "const" in names[i+2]:
            return True
    return False


def check_inv(names):
    """Returns True if two sequential tokens are inverse unary operators."""

    for i, name in enumerate(names[:-1]):
        if name in INVERSE_TOKENS and names[i+1] == INVERSE_TOKENS[name]:
            return True
    return False


def check_trig(names):
    """Returns True if a descendant of a trig operator is another trig
    operator."""
        
    trig_descendant = False # True when current node is a descendant of a trig operator

    for name in names:
        if name in TRIG_TOKENS:
            if trig_descendant:
                return True
            trig_descendant = True
            trig_dangling   = 1
        elif trig_descendant:
            if name in BINARY_TOKENS:
                trig_dangling += 1
            elif name not in UNARY_TOKENS:
                trig_dangling -= 1
            if trig_dangling == 0:
                trig_descendant = False
                
    return False

### <<<

def checkConstraint(max_length, min_length, max_depth):
    """Check a varety of constraints on a memeber. These include:
        Max Length, Min Length, Max Depth, Trig Ancestors and inversion repetes. 
        
        This is a decorator function that attaches to mutate or mate functions in
        DEAP.
    """
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            keep_inds   = [copy.deepcopy(ind) for ind in args]      # The input individual(s) before the wrapped function is called 
            new_inds    = list(func(*args, **kwargs))               # Calls the wrapped function and returns results
                        
            for i, ind in enumerate(new_inds):
                
                l = len(ind)
                
                if l > max_length:
                    new_inds[i] = random.choice(keep_inds)
                elif l < min_length:
                    new_inds[i] = random.choice(keep_inds)
                elif operator.attrgetter("height")(ind) > max_depth:
                    new_inds[i] = random.choice(keep_inds)
                else:  
                    names = [node.name for node in new_inds[i]]
                    
                    if check_inv(names):
                        new_inds[i] = random.choice(keep_inds)
                    elif check_trig(names):
                        new_inds[i] = random.choice(keep_inds)
                    
            return new_inds

        return wrapper

    return decorator


def popConstraint():
    """Check a varety of constraints on a member. These include:
        
        This is a decorator function that attaches to the individual function in
        DEAP.
    """
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):

            while(True):
                inds    = func(*args, **kwargs)               # Calls the wrapped function and returns results
                names   = [node.name for node in inds]
                    
                if check_inv(names):
                    continue
                elif check_trig(names):
                    continue
                else:
                    break
                    
            return inds

        return wrapper

    return decorator


def DEAP_to_tokens(individual, tokens_size):
        
    assert gp is not None, "Must import Deap GP library to use method. You may need to install it."
    assert isinstance(individual, gp.PrimitiveTree), "Program tokens should be a Deap GP PrimativeTree object."

    l = min(len(individual),tokens_size)
  
    tokens = np.zeros(tokens_size,dtype=np.int32)
    
    for i in range(l):
        
        t = individual[i]
        
        if isinstance(t, gp.Terminal):
            if t.name is "user_const":
                # Get the constant token, this will not store the actual const (TO DO, fix somehow)
                tokens[i] = t.value
            elif t.name is "mutable_const":
                tokens[i] = Program.library.const_token
            else:
                # Get the int which is contained in "ARG{}",
                tokens[i] = int(t.name[3:])
        else:
            # Get the index number for this op from the op list in Program.library
            tokens[i] = Program.library.names.index(t.name)
            
    arities         = np.array([Program.library.arities[t] for t in tokens])
    dangling        = 1 + np.cumsum(arities - 1) 
    expr_length     = 1 + np.argmax(dangling == 0)
  
    return tokens, expr_length


def tokens_to_DEAP(tokens, primitive_set):
    """
    Transforms DSR standard tokens into DEAP format tokens.

    DSR and DEAP format are very similar, but we need to translate it over. 

    Parameters
    ----------
    tokens : list of integers
        A list of integers corresponding to tokens in the library. The list
        defines an expression's pre-order traversal. "Dangling" programs are
        completed with repeated "x1" until the expression completes.

    primitive_set : gp.PrimitiveSet
        This should contain the list of primitives we will use. One way to create this is:
        
            # Create the primitive set
            pset = gp.PrimitiveSet("MAIN", dataset.X_train.shape[1])

            # Add input variables
            rename_kwargs = {"ARG{}".format(i) : "x{}".format(i + 1) for i in range(dataset.n_input_var)}
            pset.renameArguments(**rename_kwargs)

            # Add primitives
            for k, v in function_map.items():
                if k in dataset.function_set:
                    pset.addPrimitive(v.function, v.arity, name=v.name) 

    Returns
    _______
    individual : gp.PrimitiveTree
        This is a specialized list that contains points to element from primitive_set that were mapped based 
        on the translation of the tokens. 
    """
        
    assert gp is not None, "Must import Deap GP library to use method. You may need to install it."
    assert isinstance(tokens, np.ndarray), "Raw tokens are supplied as a numpy array."
    assert isinstance(primitive_set, gp.PrimitiveSet), "You need to supply a valid primitive set for translation."
    assert Program.library is not None, "You have to have an initial program class to supply library token conversions."
    
    '''
        Truncate expressions that complete early; extend ones that don't complete
    '''
    tokens  = _finish_tokens(tokens)
             
    plist   = []        
    
    for t in tokens:
        
        node = Program.library[t]

        if isinstance(node, float) or isinstance(node, np.float):
            '''
                NUMBER - Library supplied floating point constant. 
                    
                    This is a constant the user sets and should not change. 
            '''
            try:
                p = primitive_set.mapping["user_const"]
                p.value = node
                plist.append(p)
            except ValueError:
                print("ERROR: Cannot add \"const\" from DEAP primitve set")
                
        elif isinstance(node, str):
            '''
                NUMBER - Blank floating point constant. 
                    
                    Typically this is a constant parameter we want to optimize.
            '''
            try:
                p = primitive_set.mapping["mutable_const"]
                p.value = 1.0 #node
                plist.append(p)
            except ValueError:
                print("ERROR: Cannot add \"const\" from DEAP primitve set")
                
        elif isinstance(node, int):
            '''
                NUMBER - Values from input X at location given by value in node
                
                    This is usually the raw data point numerical values. Its value should not change. 
            '''
            try:
                plist.append(primitive_set.mapping["x{}".format(node+1)])
            except ValueError:
                print("ERROR: Cannot add argument value \"x{}\" from DEAP primitve set".format(node))
                
        else:
            '''
                FUNCTION - Name should map from Program. Be sure to add all function map items into PrimativeSet before call. 
                
                    This is any common function with a name like "sin" or "log". 
                    We assume right now all functions work on floating points. 
            '''
            try:
                plist.append(primitive_set.mapping[node.name])
            except ValueError:
                print("ERROR: Cannot add function \"{}\" from DEAP primitve set".format(node.name))
            
    individual = gp.PrimitiveTree(plist)
    
    '''
        Look. You've got it all wrong. You don't need to follow me. 
        You don't need to follow anybody! You've got to think for yourselves. 
        You're all individuals! 
    '''
    return individual


# library_length = program.L. This should be folded into task
def generate_priors(tokens, max_exp_length, expr_length, max_const, max_len, min_len):
        
    priors              = np.zeros((max_exp_length, Program.library.L), dtype=np.float32)
    
    trig_descendant     = False
    const_tokens        = 0
    dangling            = 1
    offset              = 1 # Put in constraint at t+1
    
    # Never start with a terminal token
    priors[0, Program.library.terminal_tokens] = -np.inf
        
    for i,t in enumerate(tokens): 
        
        dangling    += Program.library.arities[t] - 1
        
        '''
            Note, actions == tokens
        '''
        '''
        if (dangling == 1) & (np.sum(np.isin(tokens, Program.library.float_tokens), axis=1) == 0):
            priors[i+offset, Program.library.float_tokens] = -np.inf
        '''
        # check trig descendants
        
        if i < len(tokens) - 1:
            
            if t in Program.library.trig_tokens:
                trig_descendant = True
                trig_dangling   = 1
            elif trig_descendant:
                if t in Program.library.binary_tokens:
                    trig_dangling += 1
                elif t not in Program.library.unary_tokens:
                    trig_dangling -= 1
                    
                if trig_dangling == 0:
                    trig_descendant = False
            
            if trig_descendant:
                priors[i+offset, Program.library.trig_tokens] = -np.inf
                
            '''
            if i < len(tokens) - 2:
                if t in Program.binary_tokens:
                    if tokens[i+1] == Program.const_token:
                        priors[i+2, Program.const_token] = -np.inf     # The second token cannot be const if the first is        
            '''

            if t in Program.library.inverse_tokens:
                priors[i+offset, Program.library.inverse_tokens[t]] = -np.inf    # The second token cannot be inv the first one 
                
            '''
            if t in Program.library.unary_tokens:
                priors[i+offset, Program.library.const_token] = -np.inf         # Cannot have const inside unary token

            if t == Program.library.const_token:
                const_tokens += 1
                if const_tokens >= max_const:
                    priors[i+offset:, Program.const_token] = -np.inf      # Cap the number of consts
            '''
            # Constrain terminals
            if (i + 2) < min_len and dangling == 1:
                priors[i+offset, Program.library.terminal_tokens] = -np.inf
                
            if (i + 2) >= max_len // 2:
                remaining   = max_len - (i + 1)
                
                if dangling >= remaining - 1:
                    priors[i+offset, Program.library.binary_tokens] = -np.inf
                elif dangling == remaining:
                    priors[i+offset, Program.library.unary_tokens]  = -np.inf
             
    return priors


# This should be replaced by the task provided metric
def make_fitness(metric):
        """Generates a fitness function by name"""

        if metric == "mse":
            fitness = lambda y, y_hat, var_y : np.mean((y - y_hat)**2)

        elif metric == "rmse":
            fitness = lambda y, y_hat, var_y : np.sqrt(np.mean((y - y_hat)**2))

        elif metric == "nmse":
            fitness = lambda y, y_hat, var_y : np.mean((y - y_hat)**2 / var_y)

        elif metric == "nrmse":
            fitness = lambda y, y_hat, var_y : np.sqrt(np.mean((y - y_hat)**2 / var_y))

        else:
            raise ValueError("Metric not recognized.")

        return fitness
    

class GenericEvaluate(gp_base.GenericEvaluate):
    
    def __init__(self, const_opt, dataset, fitness_metric="nmse",
                 optimize=True, early_stopping=False, threshold=1e-12):
        
        super(GenericEvaluate, self).__init__(early_stopping=early_stopping, threshold=threshold)

        fitness                 = make_fitness(fitness_metric)
        self.X_train            = dataset.X_train.T
        self.X_test             = dataset.X_test.T
        self.y_train            = dataset.y_train
              
        self.train_fitness      = partial(fitness, y=dataset.y_train, var_y=np.var(dataset.y_train))
        self.test_fitness       = partial(fitness, y=dataset.y_test,  var_y=np.var(dataset.y_test)) # Function of y_hat

        self.const_opt          = const_opt
        self.optimize           = optimize
    
    def _set_const_individuals(self, const_idxs, consts, individual):
        
        for i, const in zip(const_idxs, consts):
            individual[i] = gp.Terminal(const, False, object)
            individual[i].name = "mutable_const" # For good measure
            
        return individual
    
    def __call__(self, individual):

        assert self.toolbox is not None, "Must set toolbox first."

        if self.optimize:
            # Retrieve symbolic constants
            const_idxs = [i for i, node in enumerate(individual) if node.name == "mutable_const"]

            # HACK: If early stopping threshold has been reached, don't do training optimization
            # Check if best individual has NMSE below threshold on test set
            if self.early_stopping and len(self.hof) > 0 and self._finish_eval(self.hof[0], self.X_test, self.test_fitness)[0] < self.threshold:
                return (1.0,)

        if self.optimize and len(const_idxs) > 0:

            # Objective function for evaluating constants
            def obj(consts):        
                individual = self._set_const_individuals(self, const_idxs, consts, individual)        

                with warnings.catch_warnings():
                    warnings.simplefilter("ignore")
                    f       = self.toolbox.compile(expr=individual)
                    
                    # Sometimes this evaluation can fail. If so, return largest error possible.
                    try:
                        y_hat   = f(*self.X_train)
                    except:
                        return np.finfo(np.float).max
                    
                    y       = self.y_train
                    res     = np.mean((y - y_hat)**2)
                    
                # Sometimes this evaluation can fail. If so, return largest error possible.
                if np.isfinite(res):
                    return res
                else:
                    return np.finfo(np.float).max

            # Do the optimization and set the optimized constants
            x0                  = np.ones(len(const_idxs))
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                optimized_consts    = self.const_opt(obj, x0)
            
            individual = self._set_const_individuals(self, const_idxs, optimized_consts, individual) 

        return self._finish_eval(individual, self.X_train, self.train_fitness)
        
        
class GPController(gp_base.GPController):
    
    def __init__(self, config_gp_meld, config_task, config_training):
        
        assert gp is not None, "Did not import gp. Is DEAP installed?"
        
        config_dataset              = config_task["dataset"]
        dataset                     = BenchmarkDataset(**config_dataset)
        pset, const_opt             = self._create_primitive_set(dataset, config_training)                                         
        eval_func                   = GenericEvaluate(const_opt, dataset, fitness_metric=config_gp_meld["fitness_metric"]) 
        check_constraint            = checkConstraint
        
        super(GPController, self).__init__(config_gp_meld, config_task, config_training, dataset, pset, eval_func, check_constraint, eval_func.hof)
        
        self.get_top_n_programs     = get_top_n_programs
        self.get_top_program        = get_top_program        
        self.tokens_to_DEAP         = tokens_to_DEAP

    def _create_primitive_set(self, dataset, config_training):
        """Create a DEAP primitive set from DSR functions and consts
        """
        
        assert gp is not None,              "Did not import gp. Is it installed?"
        assert isinstance(dataset, object), "dataset should be a DSR Dataset object" 
        
        const_params                = config_training['const_params']
        have_const                  = "const" in dataset.function_set  
        const_optimizer             = "scipy"
        
        pset                        = gp.PrimitiveSet("MAIN", dataset.X_train.shape[1])
    
        # Add input variables
        rename_kwargs = {"ARG{}".format(i) : "x{}".format(i + 1) for i in range(dataset.n_input_var)}
        pset.renameArguments(**rename_kwargs)
    
        # Add primitives
        for k, v in function_map.items():
            if k in dataset.function_set:
                pset.addPrimitive(v.function, v.arity, name=v.name)    
            
        # Are we optimizing a const?               
        if have_const:
            # Need to differentiate between mutable and non mutable const
            const_params    = const_params if const_params is not None else {}
            const_opt       = make_const_optimizer(const_optimizer, **const_params)
            pset.addTerminal(1.0, name="mutable_const")  
            pset.addTerminal(1.0, name="user_const")   
        else:
            const_opt       = None   
            
        return pset, const_opt

    def _create_toolbox(self, pset, eval_func, max_const=None, constrain_const=False, **kwargs):
                
        toolbox, creator    = self._base_create_toolbox(pset, eval_func, **kwargs) 
        const               = "const" in pset.context
        
        if const and max_const is not None:
            assert isinstance(max_const,int)
            assert max_const >= 0
            num_const = lambda ind : len([node for node in ind if node.name == "mutable_const"])
            toolbox.decorate("mate",        gp.staticLimit(key=num_const, max_value=max_const))
            toolbox.decorate("mutate",      gp.staticLimit(key=num_const, max_value=max_const))
    
        if const and constrain_const is True:
            toolbox.decorate("mate",        gp.staticLimit(key=check_const, max_value=0))
            toolbox.decorate("mutate",      gp.staticLimit(key=check_const, max_value=0))
        
        return toolbox, creator
    
 
def convert_inverse_prim(prim, args):
    """
    Convert inverse prims according to:
    [Dd]iv(a,b) -> Mul[a, 1/b]
    [Ss]ub(a,b) -> Add[a, -b]
    We achieve this by overwriting the corresponding format method of the sub and div prim.
    """
    prim = copy.copy(prim)
    #prim.name = re.sub(r'([A-Z])', lambda pat: pat.group(1).lower(), prim.name)    # lower all capital letters

    converter = {
        'sub': lambda *args_: "Add({}, Mul(-1,{}))".format(*args_),
        'protectedDiv': lambda *args_: "Mul({}, Pow({}, -1))".format(*args_),
        'div': lambda *args_: "Mul({}, Pow({}, -1))".format(*args_),
        'mul': lambda *args_: "Mul({},{})".format(*args_),
        'add': lambda *args_: "Add({},{})".format(*args_),
        'inv': lambda *args_: "Pow(-1)".format(*args_),
        'neg': lambda *args_: "Mul(-1)".format(*args_)
    }
    prim_formatter = converter.get(prim.name, prim.format)

    return prim_formatter(*args)


def stringify_for_sympy(f):
    """Return the expression in a human readable string.
    """
    string = ""
    stack = []
    for node in f:
        stack.append((node, []))
        while len(stack[-1][1]) == stack[-1][0].arity:
            prim, args = stack.pop()
            string = convert_inverse_prim(prim, args)
            if len(stack) == 0:
                break  # If stack is empty, all nodes should have been seen
            stack[-1][1].append(string)
    return string


def get_top_program(halloffame, actions, config_gp_meld):
    """ In addition to returning the best program, this will also compute DSR compatible parents, siblings and actions.
    """
    
    max_const   = config_gp_meld["max_const"]
    max_len     = config_gp_meld["max_len"] # <-- fold into base
    min_len     = config_gp_meld["min_len"] # <-- fold into base
    
    deap_program, deap_obs, deap_action, deap_tokens, deap_expr_length      = gp_base._get_top_program(halloffame, actions, max_len, min_len, DEAP_to_tokens)
    deap_prior                                                              = generate_priors(deap_tokens, actions.shape[1], deap_expr_length, max_const, max_len, min_len)

    return deap_program, deap_obs, deap_action, deap_prior
    
    
def get_top_n_programs(population, actions, config_gp_meld):
    """ Get the top n members of the population, We will also do some things like remove 
        redundant members of the population, which there tend to be a lot of.
        
        Next we compute DSR compatible parents, siblings and actions.  
    """
    
    n           = config_gp_meld["train_n"] # <-- fold into base
    max_const   = config_gp_meld["max_const"]
    max_len     = config_gp_meld["max_len"] # <-- fold into base
    min_len     = config_gp_meld["min_len"] # <-- fold into base
    
    max_tok     = Program.library.L

    deap_program, deap_obs, deap_action, deap_tokens, deap_expr_length      = gp_base._get_top_n_programs(population, n, actions, max_len, min_len, DEAP_to_tokens)
    deap_priors                                                             = np.empty((len(deap_tokens), actions.shape[1], max_tok), dtype=np.float32)
        
    for i in range(len(deap_tokens)):        
        deap_priors[i,]                 = generate_priors(deap_tokens[i], actions.shape[1], deap_expr_length[i], max_const, max_len, min_len)

    return deap_program, deap_obs, deap_action, deap_priors

