"""Tigramite causal discovery for time series."""

# Authors: Wiebke Günther <wiebke.guenther@dlr.de>, Urmi Ninad, Jakob Runge <jakob@jakob-runge.com>
#
# License: GNU General Public License v3.0

from __future__ import print_function
import numpy as np
from tigramite.pcmci import PCMCI
from tigramite.independence_tests.parcorr_mult import ParCorrMult
from copy import deepcopy
import itertools

from tigramite.toymodels.context_model import group_links


class J_PCMCIplus(PCMCI):
    r"""J-PCMCI+ causal discovery for time series datasets from multiple contexts.
        This class is based on the PCMCI framework as described in [1]_.
        J_PCMCIplus enables causal discovery for time series data from different contexts,
        i.e. datasets, where some of the variables describing the context might be unobserved.
        The method is described in detail in [10]_.
        See the tutorial for guidance in applying the method.

        References
        ----------
        .. [1] J. Runge, P. Nowack, M. Kretschmer, S. Flaxman, D. Sejdinovic,
               Detecting and quantifying causal associations in large nonlinear time
               series datasets. Sci. Adv. 5, eaau4996 (2019)
               https://advances.sciencemag.org/content/5/11/eaau4996
        .. [10] W. Günther, U. Ninad, J. Runge,
               Causal discovery for time series from multiple datasets with latent contexts. UAI 2023
        Parameters
        ----------
        node_classification : dictionary
            Classification of nodes into system, context, or dummy nodes.
            Keys of the dictionary are from {0, ..., N-1} where N is the number of nodes.
            Options for the values are "system", "time_context", "space_context", "time_dummy", or "space_dummy".
        Attributes
        ----------
        all_parents : dictionary
            Dictionary of form {0:[(0, -1), (3, -2), ...], 1:[], ...} containing
            the conditioning-parents estimated with PC algorithm.
        val_min : dictionary
            Dictionary of form val_min[j][(i, -tau)] = float
            containing the minimum test statistic value for each link estimated in
            the PC algorithm.
        pval_max : dictionary
            Dictionary of form pval_max[j][(i, -tau)] = float containing the maximum
            p-value for each link estimated in the PC algorithm.
        iterations : dictionary
            Dictionary containing further information on algorithm steps.
        N : int
            Number of variables.
        T : dict
            Time series sample length of dataset(s).
        dummy_parents : dictionary or None
            Dictionary of form {0:[(0, -1), (3, -2), ...], 1:[], ...} containing
            the dependence of the system nodes on the dummy nodes.
        observed_context_parents : dictionary or None
            Dictionary of form {0:[(0, -1), (3, -2), ...], 1:[], ...} containing
            the dependence of the system nodes on the observed context nodes.
        dummy_ci_test : conditional independence test object
            Conditional independence test used to test dependence between system nodes and dummy nodes.
            Currently, ParCorr is used with one-hot encoded dummies.
        mode : "system_search" or "context_search" or "dummy_search" (default: "system_search")
        time_context_nodes : list
            List with entries from {0, ..., N-1} where N is the number of nodes.
            This is the list of the temporal context nodes which are assumed to be constant over the different datasets.
        space_context_nodes :
            List with entries from {0, ..., N-1} where N is the number of nodes.
            This is the list of the spatial context nodes which are assumed to be constant over time.
        time_dummy : int or None (default: None)
            Node corresponding to the temporal dummy variable.
        space_dummy : int or None (default: None)
            Node corresponding to the spatial dummy variable.
        system_nodes : list
            List with entries from {0, ..., N-1} where N is the number of nodes.
            This is the list of the system nodes.
        """

    def __init__(self, node_classification, dummy_ci_test=ParCorrMult(significance='analytic'), **kwargs):
        PCMCI.__init__(self, **kwargs)

        self.system_nodes = self.group_nodes(node_classification, "system")
        self.time_context_nodes = self.group_nodes(node_classification, "time_context")
        self.space_context_nodes = self.group_nodes(node_classification, "space_context")
        self.time_dummy = self.group_nodes(node_classification, "time_dummy")
        self.space_dummy = self.group_nodes(node_classification, "space_dummy")

        self.dummy_parents = {i: [] for i in range(self.N)}
        self.observed_context_parents = {i: [] for i in range(self.N)}
        self.dummy_ci_test = dummy_ci_test
        self.mode = "system_search"

    def group_nodes(self, node_types, node_type):
        nodes = range(self.N)
        return [node for node in nodes if node_types[node] == node_type]

    def run_jpcmciplus(self,
                       contemp_collider_rule='majority',
                       link_assumptions=None,
                       tau_min=0,
                       tau_max=2,
                       pc_alpha=0.01,
                       conflict_resolution=True,
                       reset_lagged_links=False,
                       max_conds_dim=None,
                       max_combinations=1,
                       max_conds_py=None,
                       max_conds_px=None,
                       max_conds_px_lagged=None,
                       fdr_method='none'):
        """
        Runs J_PCMCIplus time-lagged and contemporaneous causal discovery for time series from multiple contexts.
        Method described in [10]_:
            W. Günther, U. Ninad, J. Runge,
            Causal discovery for time series from multiple datasets with latent contexts. UAI 2023
        Notes
        -----
        The J_PCMCIplus causal discovery method is described in [10]_, where
        also analytical and numerical results are presented. J_PCMCIplus can identify the joint causal graph
        over multiple datasets containing time series data from different contexts under the standard assumptions
        of Causal Sufficiency, Faithfulness and the Markov condition, as well as some background knowledge assumptions.
        J_PCMCIplus estimates time-lagged and contemporaneous causal links from context to system
        variables and in between system variables by a four-step procedure:

        1.  **Discovery of supersets of the lagged parents of the system and observed temporal context nodes** by
        running the :math:`PC_1` lagged phase on this subset of nodes.

        2.  Next, the **MCI test is run on pairs of system and context nodes conditional on subsets of system
        and context**, i.e. perform MCI tests for pairs :math:`((C^j_{t-\\tau}, X^i_t))_{\\tau > 0}`,
        :math:`(C_t^j, X_t^i)`, :math:`(X_t^i, C_t^j)` for all :math:`i,j`,

        .. math:: C_{t-\\tau}^i \\perp X_t^j | \\mathbf{S}, \\hat{\\mathcal{B}}^-_t(X_t^j)
                    \\setminus \\{ C_{t-\\tau}^i \\}, \\hat{\\mathcal{B}}^-_{t-\\tau}(C_{t-\\tau}^i)

        with :math:`\\mathbf{S}` being a subset of the contemporaneous adjacencies :math:`\\mathcal{A}_t(X_t^j)` and
        :math:`\\hat{\\mathcal{B}}^-_t(X_t^j)` are the lagged adjacencies from step one. If :math:`C` is a spatial context variable,
        we only have to test the contemporaneous pairs :math:`(C_t^j, X_t^i)`, :math:`(X_t^i, C_t^j)` for all :math:`i,j`.
        If :math:`C_t^j` and :math:`X_t^i` are conditionally independent, all lagged links between :math:`C_t^j` and
        :math:`X^j_{t-\\tau}` are also removed for all :math:`\\tau`.

        3.  **Perform MCI tests on all system-dummy pairs conditional on the superset of lagged links, the discovered
        contemporaneous context adjacencies, as well as on subsets of contemporaneous system links**, i.e. test
        for :math:`(D, X_t^i)`, :math:`(X_t^i, D)` for all :math:`i`, i.e.

        .. math:: D \\perp X_t^j | \\mathbf{S}, \\hat{\\mathcal{B}}^C_t(X_t^j),

        where :math:`\\mathbf{S} \\subset \\mathcal{A}_t(X_t^i)` and :math:`\\hat{\\mathcal{B}}^C_t(X_t^j)`
        are the lagged and contextual adjacencies found in the previous step.
        If :math:`D` and :math:`X_t^j` are found to be conditionally independence, links between :math:`D` and
        :math:`X^j_{t-\\tau}` are removed for all :math:`\\tau`.
        By assumption context node is the parent in all system-context links.

        4.  Finally, we **perform  MCI tests on all system pairs conditional on discovered lagged, context and dummy
        adjacencies, as well as on subsets of contemporaneous system links** and **orientation phase**. In more detail,
        we perform MCI test for pairs :math:`((X^j_{t-\\tau}, X_t^i))_{\\tau > 0}`, :math:`(X_t^i, X_t^j)` for all
        :math:`i, j`, i.e.

        .. math:: X^i_{t-\\tau} \\perp X_t^j | \\mathbf{S}, \\hat{\\mathcal{B}}^{CD}_t(X_t^j)
                    \\setminus \\{ X_{t-\\tau}^i \\},\\hat{\\mathcal{B}}^{CD}_t(X_{t-\\tau}^i)

        where :math:`\\mathbf{S} \\subset \\mathcal{A}_t(X_t^i)` and :math:`\\hat{\\mathcal{B}}^{CD}_t(X_t^j)`
        are the lagged, contextual, and dummy adjacencies found in the previous steps.
        Finally, all remaining edges (without expert knowledge) are oriented using the PCMCI+ orientation phase while
        making use of all triples involving one context or dummy variable and two system variables as in the non-time
        series case.

        J_PCMCIplus can be flexibly combined with any kind of conditional
        independence test statistic adapted to the kind of data (continuous
        or discrete) and its assumed dependency types. These are available in
        ``tigramite.independence_tests``.
        See PCMCIplus for a description of the parameters of J_PCMCIplus. Also, guidance on best practices for
        setting these parameters is given there.

        Parameters
        ----------
        link_assumptions : dict
            Dictionary of form {j:{(i, -tau): link_type, ...}, ...} specifying
            assumptions about links. This initializes the graph with entries
            graph[i,j,tau] = link_type. For example, graph[i,j,0] = '-->'
            implies that a directed link from i to j at lag 0 must exist.
            Valid link types are 'o-o', '-->', '<--'. In addition, the middle
            mark can be '?' instead of '-'. Then '-?>' implies that this link
            may not exist, but if it exists, its orientation is '-->'. Link
            assumptions need to be consistent, i.e., graph[i,j,0] = '-->'
            requires graph[j,i,0] = '<--' and acyclicity must hold. If a link
            does not appear in the dictionary, it is assumed absent. That is,
            if link_assumptions is not None, then all links have to be specified
            or the links are assumed absent.
        tau_min : int, optional (default: 0)
            Minimum time lag to test.
        tau_max : int, optional (default: 1)
            Maximum time lag. Must be larger or equal to tau_min.
        pc_alpha : float or list of floats, default: 0.01
            Significance level in algorithm. If a list or None is passed, the
            pc_alpha level is optimized for every graph across the given
            pc_alpha values ([0.001, 0.005, 0.01, 0.025, 0.05] for None) using
            the score computed in cond_ind_test.get_model_selection_criterion().
        contemp_collider_rule : {'majority', 'conservative', 'none'}
            Rule for collider phase to use. See the paper for details. Only
            'majority' and 'conservative' lead to an order-independent
            algorithm.
        conflict_resolution : bool, optional (default: True)
            Whether to mark conflicts in orientation rules. Only for True
            this leads to an order-independent algorithm.
        reset_lagged_links : bool, optional (default: False)
            Restricts the detection of lagged causal links in Step 2 to the
            significant adjacencies found in the PC1 algorithm in Step 1. For
            True, *all* lagged links are considered again, which improves
            detection power for lagged links, but also leads to larger
            runtimes.
        max_conds_dim : int, optional (default: None)
            Maximum number of conditions to test. If None is passed, this number
            is unrestricted.
        max_combinations : int, optional (default: 1)
            Maximum number of combinations of conditions of current cardinality
            to test. Defaults to 1 for PC_1 algorithm. For original PC algorithm
            a larger number, such as 10, can be used.
        max_conds_py : int, optional (default: None)
            Maximum number of lagged conditions of Y to use in MCI tests. If
            None is passed, this number is unrestricted.
        max_conds_px : int, optional (default: None)
            Maximum number of lagged conditions of X to use in MCI tests. If
            None is passed, this number is unrestricted.
        max_conds_px_lagged : int, optional (default: None)
            Maximum number of lagged conditions of X when X is lagged in MCI
            tests. If None is passed, this number is equal to max_conds_px.
        fdr_method : str, optional (default: 'none')
            Correction method, default is Benjamini-Hochberg False Discovery
            Rate method.
        Returns
        -------
        graph : array of shape [N, N, tau_max+1]
            Resulting causal graph, see description above for interpretation.
        val_matrix : array of shape [N, N, tau_max+1]
            Estimated matrix of test statistic values regarding adjacencies.
        p_matrix : array of shape [N, N, tau_max+1]
            Estimated matrix of p-values regarding adjacencies.
        sepset : dictionary
            Separating sets. See paper for details.
        ambiguous_triples : list
            List of ambiguous triples, only relevant for 'majority' and
            'conservative' rules, see paper for details.
        """
        observed_context_nodes = self.time_context_nodes + self.space_context_nodes

        # initialize / clean link_assumptions
        if link_assumptions is not None:
            _link_assumptions = deepcopy(link_assumptions)
        else:
            _link_assumptions = {j: {(i, -tau): 'o?o' for i in range(self.N)
                                     for tau in range(tau_max + 1)} for j in range(self.N)}

        _link_assumptions = self.assume_exogeneous_context(_link_assumptions, observed_context_nodes)
        _link_assumptions = self.clean_link_assumptions(_link_assumptions, tau_max)

        # Check if pc_alpha is chosen to optimize over a list
        if pc_alpha is None or isinstance(pc_alpha, (list, tuple, np.ndarray)):
            # Call optimizer wrapper around run_pcmciplus()
            return self._optimize_pcmciplus_alpha(
                link_assumptions=link_assumptions,
                tau_min=tau_min,
                tau_max=tau_max,
                pc_alpha=pc_alpha,
                contemp_collider_rule=contemp_collider_rule,
                conflict_resolution=conflict_resolution,
                reset_lagged_links=reset_lagged_links,
                max_conds_dim=max_conds_dim,
                max_combinations=max_combinations,
                max_conds_py=max_conds_py,
                max_conds_px=max_conds_px,
                max_conds_px_lagged=max_conds_px_lagged,
                fdr_method=fdr_method)

        elif pc_alpha < 0. or pc_alpha > 1:
            raise ValueError("Choose 0 <= pc_alpha <= 1")

        # Check the limits on tau
        self._check_tau_limits(tau_min, tau_max)

        # Step 0 and 1:
        context_results = self.discover_lagged_context_system_links(
            _link_assumptions,
            tau_min=tau_min,
            tau_max=tau_max,
            pc_alpha=pc_alpha,
            reset_lagged_links=reset_lagged_links,
            max_conds_dim=max_conds_dim,
            max_combinations=max_combinations,
            max_conds_py=max_conds_py,
            max_conds_px=max_conds_px,
            max_conds_px_lagged=max_conds_px_lagged,
            fdr_method=fdr_method
        )
        ctxt_res = deepcopy(context_results)
        # Store the parents in the pcmci member
        self.observed_context_parents = deepcopy(context_results['parents'])
        self.all_lagged_parents = deepcopy(context_results['lagged_parents'])

        if self.verbosity > 0:
            print("Discovered observed context parents: ", context_results['parents'])

        if len(self.time_dummy) > 0 or len(self.space_dummy) > 0:
            # step 2:
            dummy_system_results = self.discover_dummy_system_links(
                _link_assumptions,
                ctxt_res,
                self.all_lagged_parents,
                tau_min=tau_min,
                tau_max=tau_max,
                pc_alpha=pc_alpha,
                reset_lagged_links=reset_lagged_links,
                max_conds_dim=max_conds_dim,
                max_conds_py=max_conds_py,
                max_conds_px=max_conds_px,
                max_conds_px_lagged=max_conds_px_lagged,
                fdr_method=fdr_method
            )
            # Store the parents in the pcmci member
            self.dummy_parents = dummy_system_results['parents']
        else:
            dummy_system_results = deepcopy(context_results)

        if self.verbosity > 0:
            print("Discovered dummy parents: ", self.dummy_parents)

        # step 3:
        self.mode = "system_search"
        lagged_context_dummy_parents = {
            i: list(
                dict.fromkeys(self.all_lagged_parents[i] + self.observed_context_parents[i] + self.dummy_parents[i]))
            for i in range(self.N)}

        dummy_system_results_copy = deepcopy(dummy_system_results)

        system_skeleton_results = self.discover_system_system_links(link_assumptions=_link_assumptions,
                                                                    lagged_context_dummy_parents=lagged_context_dummy_parents,
                                                                    tau_min=tau_min,
                                                                    tau_max=tau_max,
                                                                    pc_alpha=pc_alpha,
                                                                    reset_lagged_links=reset_lagged_links,
                                                                    max_conds_dim=max_conds_dim,
                                                                    max_conds_py=max_conds_py,
                                                                    max_conds_px=max_conds_px,
                                                                    max_conds_px_lagged=max_conds_px_lagged,
                                                                    fdr_method=fdr_method)

        # orientation phase
        colliders_step_results = self._pcmciplus_collider_phase(
            system_skeleton_results['graph'], system_skeleton_results['sepsets'],
            lagged_context_dummy_parents, pc_alpha,
            tau_min, tau_max, max_conds_py, max_conds_px, max_conds_px_lagged,
            conflict_resolution, contemp_collider_rule)

        final_graph = self._pcmciplus_rule_orientation_phase(colliders_step_results['graph'],
                                                             colliders_step_results['ambiguous_triples'],
                                                             conflict_resolution)

        # add context-system and dummy-system values and pvalues back in (lost because of link_assumption)
        for c in observed_context_nodes + self.time_dummy + self.space_dummy:
            for j in range(self.N):
                for lag in range(tau_max + 1):
                    # add context-system links to results
                    system_skeleton_results['val_matrix'][c, j, lag] = dummy_system_results_copy['val_matrix'][
                        c, j, lag]
                    system_skeleton_results['val_matrix'][j, c, lag] = dummy_system_results_copy['val_matrix'][
                        j, c, lag]

                    system_skeleton_results['p_matrix'][c, j, lag] = dummy_system_results_copy['p_matrix'][c, j, lag]
                    system_skeleton_results['p_matrix'][j, c, lag] = dummy_system_results_copy['p_matrix'][j, c, lag]

        # No confidence interval estimation here
        return_dict = {'graph': final_graph, 'p_matrix': system_skeleton_results['p_matrix'],
                       'val_matrix': system_skeleton_results['val_matrix'],
                       'sepsets': colliders_step_results['sepsets'],
                       'ambiguous_triples': colliders_step_results['ambiguous_triples'], 'conf_matrix': None}

        # Print the results
        if self.verbosity > 0:
            self.print_results(return_dict, alpha_level=pc_alpha)

        # Return the dictionary
        self.results = return_dict

        # Return the dictionary
        return return_dict

    def assume_exogeneous_context(self, link_assumptions, observed_context_nodes):
        """Helper function to amend the link_assumptions to ensure that all context-system links are oriented
        such that the context variable is the parent."""
        for j in link_assumptions:
            if j in self.system_nodes:
                for link in link_assumptions[j]:
                    i, lag = link
                    if i in observed_context_nodes + self.time_dummy + self.space_dummy:  # is context var
                        link_type = link_assumptions[j][link]
                        link_assumptions[j][link] = '-' + link_type[1] + '>'
        return link_assumptions

    def clean_link_assumptions(self, link_assumptions, tau_max):
        """Helper function to amend the link_assumptions in the following ways
            * remove any links where dummy is the child
            * remove any lagged links to dummy, and space_context (not to observed time context)
            * and system - context links where context is the child
            * and any links between spatial and temporal context
        """
        for node in self.time_dummy + self.space_dummy:
            link_assumptions[node] = {}

        for j in self.system_nodes + self.time_context_nodes + self.space_context_nodes:
            for lag in range(1, tau_max + 1):
                for c in self.time_dummy + self.space_dummy + self.space_context_nodes:
                    if (c, -lag) in link_assumptions[j]: link_assumptions[j].pop((c, -lag), None)
        for c in self.space_context_nodes + self.time_context_nodes:
            for j in self.system_nodes:
                for lag in range(tau_max + 1):
                    if (j, -lag) in link_assumptions[c]: link_assumptions[c].pop((j, -lag), None)
            if (c, 0) in link_assumptions[c]: link_assumptions[c].pop((c, 0), None)  # remove self-links

        for c in self.space_context_nodes:
            for k in self.time_context_nodes:
                for lag in range(tau_max + 1):
                    if (k, -lag) in link_assumptions[c]: link_assumptions[c].pop((k, -lag), None)
        for c in self.time_context_nodes:
            for k in self.space_context_nodes:
                if (k, 0) in link_assumptions[c]: link_assumptions[c].pop((k, 0), None)

        return link_assumptions

    def remove_dummy_link_assumptions(self, link_assumptions):
        """Helper function to remove any links to dummy from link_assumptions."""
        link_assumptions_wo_dummy = deepcopy(link_assumptions)
        for j in self.system_nodes + self.time_context_nodes + self.space_context_nodes:
            for dummy_node in self.time_dummy + self.space_dummy:
                if (dummy_node, 0) in link_assumptions_wo_dummy[j]:
                    link_assumptions_wo_dummy[j].pop((dummy_node, 0), None)
        return link_assumptions_wo_dummy

    def add_found_context_link_assumptions(self, link_assumptions, tau_max):
        """Helper function to add discovered links between system and observed context nodes to link_assumptions."""
        link_assumptions_dummy = deepcopy(link_assumptions)

        for c in self.space_context_nodes + self.time_context_nodes:
            link_assumptions_dummy[c] = {}
        for j in self.system_nodes:
            for c in self.space_context_nodes + self.time_context_nodes:
                for lag in range(tau_max + 1):
                    if (c, -lag) in link_assumptions_dummy[j]:
                        link_assumptions_dummy[j].pop((c, -lag), None)
                    link_assumptions_dummy[j].update({parent: '-->' for parent in self.observed_context_parents[j]})
        return link_assumptions_dummy

    def clean_system_link_assumptions(self, link_assumptions, tau_max):
        """Helper function to remove any links to dummy and observed context nodes from link_assumptions.
            Add discovered links to contextual parents (from steps 1 and 2) to the link_assumptions.
        """
        dummy_vars = self.time_dummy + self.space_dummy
        observed_context_nodes = self.time_context_nodes + self.space_context_nodes
        system_links = deepcopy(link_assumptions)

        for j in self.system_nodes:
            for C in dummy_vars + observed_context_nodes:
                for lag in range(tau_max + 1):
                    if (C, -lag) in system_links[j]:
                        system_links[j].pop((C, -lag), None)

        for j in system_links:
            system_links[j].update(
                {parent: '-->' for parent in self.observed_context_parents[j] + self.dummy_parents[j]})

        for C in observed_context_nodes + dummy_vars:
            # we are not interested in links between context variables (thus system_links[C] = {})
            system_links[C] = {}
        return system_links

    def discover_lagged_context_system_links(self, link_assumptions,
                                             tau_min=0,
                                             tau_max=1, pc_alpha=0.01,
                                             reset_lagged_links=False,
                                             max_conds_dim=None,
                                             max_combinations=1,
                                             max_conds_py=None,
                                             max_conds_px=None,
                                             max_conds_px_lagged=None,
                                             fdr_method='none'):
        """
        Step 1 of J_PCMCIplus, i.e. discovery of links between observed context nodes and system nodes through an
        application of the skeleton phase of PCMCIplus to this subset of nodes (observed context nodes and system
        nodes).
        See run_jpcmciplus for a description of the parameters.

        Returns
        -------
        lagged_context_parents : dictionary
            Dictionary of form {0:[(0, -1), (3, -2), ...], 1:[], ...} containing
            the conditioning-parents estimated with PC algorithm, as well as the estimated observed context parents.
        values : array of shape [N, N, tau_max+1]
            Estimated matrix of test statistic values regarding adjacencies.
        parents : dictionary
            Dictionary of form {0:[(0, -1), (3, -2), ...], 1:[], ...} containing
            the estimated observed context parents.
        """

        # Initializing
        context_parents = {i: [] for i in range(self.N)}

        # find links in btw expressive context, and btw expressive context and sys_vars
        # here, we exclude any links to dummy
        _link_assumptions_wo_dummy = self.remove_dummy_link_assumptions(link_assumptions)
        _int_link_assumptions = self._set_link_assumptions(_link_assumptions_wo_dummy, tau_min, tau_max)

        # Step 1: Get a superset of lagged parents from run_pc_stable
        lagged_parents = self.run_pc_stable(link_assumptions=link_assumptions,
                                            tau_min=tau_min,
                                            tau_max=tau_max,
                                            pc_alpha=pc_alpha,
                                            max_conds_dim=max_conds_dim,
                                            max_combinations=max_combinations)

        self.mode = "context_search"

        p_matrix = self.p_matrix
        val_matrix = self.val_matrix

        # run PCMCI+ skeleton phase on subset of links to discover context-system links
        if self.verbosity > 0:
            print("\n##\n## J-PCMCI+ Step 1: Discovering context-system links\n##")
            if link_assumptions is not None:
                print("\nWith link_assumptions = %s" % str(_int_link_assumptions))

        skeleton_results = self._pcmciplus_mci_skeleton_phase(
            lagged_parents, _int_link_assumptions, pc_alpha,
            tau_min, tau_max, max_conds_dim, None,
            max_conds_py, max_conds_px, max_conds_px_lagged,
            reset_lagged_links, fdr_method,
            p_matrix, val_matrix
        )

        skeleton_graph = skeleton_results['graph']

        for j in self.system_nodes + self.time_context_nodes + self.space_context_nodes:
            for c in self.space_context_nodes + self.time_context_nodes:
                for k in range(tau_max + 1):
                    if skeleton_graph[c, j, k] == 'o?o' or skeleton_graph[c, j, k] == '-?>' or skeleton_graph[
                        c, j, k] == 'o-o' or skeleton_graph[c, j, k] == '-->':
                        context_parents[j].append((c, -k))

        return_dict = {'graph': skeleton_results['graph'], 'p_matrix': skeleton_results['p_matrix'],
                       'val_matrix': skeleton_results['val_matrix'],
                       'parents': context_parents, 'lagged_parents': lagged_parents}

        # Print the results
        if self.verbosity > 0:
            self.print_results(return_dict, alpha_level=pc_alpha)

        return return_dict

    def discover_dummy_system_links(self, link_assumptions,
                                    context_system_results,
                                    lagged_parents,
                                    tau_min=0,
                                    tau_max=1,
                                    pc_alpha=0.01,
                                    reset_lagged_links=False,
                                    max_conds_dim=None,
                                    max_conds_py=None,
                                    max_conds_px=None,
                                    max_conds_px_lagged=None,
                                    fdr_method='none'):
        """
        Step 2 of J_PCMCIplus, i.e. discovery of links between observed (time and space) dummy nodes and system nodes
        through an application of the skeleton phase of PCMCIplus to this subset of nodes (dummy nodes and
        system nodes).
        See run_jpcmciplus for a description of the parameters.

        Parameters
        ----------
        context_system_results : dictionary
            Output of discover_lagged_and_context_system_links, i.e. lagged and context parents together with the
            corresponding estimated test statistic values regarding adjacencies.
        lagged_parents : dictionary
            Dictionary of form {0:[(0, -1), (3, -2), ...], 1:[], ...} containing the conditioning-parents
            estimated with PC algorithm.

        Returns
        -------
        lagged_context_parents : dictionary
            Dictionary of form {0:[(0, -1), (3, -2), ...], 1:[], ...} containing
            the conditioning-parents estimated with PC algorithm, as well as the estimated (dummy and observed)
            context parents.
        values : array of shape [N, N, tau_max+1]
            Estimated matrix of test statistic values regarding adjacencies.
        parents : dictionary
            Dictionary of form {0:[(0, -1), (3, -2), ...], 1:[], ...} containing
            the estimated (dummy and observed) context parents.
        """

        lagged_context_parents = {i: context_system_results['parents'][i] + lagged_parents[i] for i in
                                  range(self.N)}
        dummy_parents = {i: [] for i in range(self.N)}
        val_matrix = context_system_results['val_matrix']
        p_matrix = context_system_results['p_matrix']

        # setup link assumptions without the observed context nodes
        _link_assumptions_dummy = self.add_found_context_link_assumptions(link_assumptions, tau_max)
        _int_link_assumptions = self._set_link_assumptions(_link_assumptions_dummy, tau_min, tau_max)

        self.mode = "dummy_search"
        # Step 2+3+4: PC algorithm with contemp. conditions and MCI tests
        if self.verbosity > 0:
            print("\n##\n## J-PCMCI+ Step 2: Discovering dummy-system links\n##")
            if _link_assumptions_dummy is not None:
                print("\nWith link_assumptions = %s" % str(_int_link_assumptions))

        skeleton_results_dummy = self._pcmciplus_mci_skeleton_phase(
            lagged_context_parents, _int_link_assumptions, pc_alpha,  # or lagged_contxt_parents?
            tau_min, tau_max, max_conds_dim, None,
            max_conds_py, max_conds_px, max_conds_px_lagged,
            reset_lagged_links, fdr_method,
            self.p_matrix, self.val_matrix
        )

        skeleton_graph_dummy = skeleton_results_dummy['graph']

        for j in self.system_nodes:
            for k in range(tau_max + 1):
                for dummy_node in self.time_dummy + self.space_dummy:
                    if skeleton_graph_dummy[dummy_node, j, k] == 'o?o' or \
                            skeleton_graph_dummy[dummy_node, j, k] == '-?>' or \
                            skeleton_graph_dummy[dummy_node, j, k] == 'o-o' or \
                            skeleton_graph_dummy[dummy_node, j, k] == '-->':
                        dummy_parents[j].append((dummy_node, k))
                for context_node in self.time_context_nodes + self.space_context_nodes:
                    skeleton_results_dummy['val_matrix'][context_node, j, k] = context_system_results['val_matrix'][
                        context_node, j, k]
                    skeleton_results_dummy['val_matrix'][j, context_node, k] = context_system_results['val_matrix'][
                        j, context_node, k]

                    skeleton_results_dummy['p_matrix'][context_node, j, k] = p_matrix[context_node, j, k]
                    skeleton_results_dummy['p_matrix'][j, context_node, k] = p_matrix[j, context_node, k]

        return_dict = {'graph': skeleton_results_dummy['graph'], 'p_matrix': skeleton_results_dummy['p_matrix'],
                       'val_matrix': skeleton_results_dummy['val_matrix'], 'parents': dummy_parents}

        # Print the results
        if self.verbosity > 0:
            self.print_results(return_dict, alpha_level=pc_alpha)
        return return_dict

    def discover_system_system_links(self, link_assumptions,
                                     lagged_context_dummy_parents,
                                     tau_min=0,
                                     tau_max=1,
                                     pc_alpha=0.01,
                                     reset_lagged_links=False,
                                     max_conds_dim=None,
                                     max_conds_py=None,
                                     max_conds_px=None,
                                     max_conds_px_lagged=None,
                                     fdr_method='none'
                                     ):
        """
        Step 4 of J_PCMCIplus and orientation phase, i.e. discovery of links between system nodes given the knowledge
        about their context parents through an application of PCMCIplus to this subset of nodes (system nodes).
        See run_jpcmciplus for a description of the other parameters.


        Parameters
        ----------
        context_results : dictionary
            Output of discover_lagged_and_context_system_links, i.e. lagged and (dummy and observed) context parents
            together with the corresponding estimated test statistic values regarding adjacencies.

        Returns
        -------
        graph : array of shape [N, N, tau_max+1]
            Resulting causal graph, see description above for interpretation.
        val_matrix : array of shape [N, N, tau_max+1]
            Estimated matrix of test statistic values regarding adjacencies.
        p_matrix : array of shape [N, N, tau_max+1]
            Estimated matrix of p-values regarding adjacencies.
        sepset : dictionary
            Separating sets. See paper for details.
        ambiguous_triples : list
            List of ambiguous triples, only relevant for 'majority' and
            'conservative' rules, see paper for details.
        """
        self.mode = "system_search"

        # Get the parents from run_pc_stable only on the system links
        system_links = self.clean_system_link_assumptions(link_assumptions, tau_max)
        # Set the selected links
        _int_link_assumptions = self._set_link_assumptions(system_links, tau_min, tau_max)

        if self.verbosity > 0:
            print("\n##\n## J-PCMCI+ Step 3: Discovering system-system links \n##")
            if system_links is not None:
                print("\nWith link_assumptions = %s" % str(_int_link_assumptions))

        skeleton_results = self._pcmciplus_mci_skeleton_phase(
            lagged_context_dummy_parents, _int_link_assumptions, pc_alpha,
            tau_min, tau_max, max_conds_dim, None,
            max_conds_py, max_conds_px, max_conds_px_lagged,
            reset_lagged_links, fdr_method,
            self.p_matrix, self.val_matrix
        )

        return skeleton_results

    def _remaining_pairs(self, graph, adjt, tau_min, tau_max, p):
        """Helper function returning the remaining pairs that still need to be
        tested depending on the J_PCMCIplus step, i.e. discovery of context-system links (step 1),
        dummy-context links (step 2) or system-system links in which case the function of the parent class is called.
        """
        all_context_nodes = self.time_context_nodes + self.space_context_nodes
        if self.mode == "context_search":
            # during discovery of context-system links we are only
            # interested in context-context and context-system pairs
            N = graph.shape[0]
            pairs = []
            for (i, j) in itertools.product(range(N), range(N)):
                for abstau in range(tau_min, tau_max + 1):
                    if (graph[i, j, abstau] != ""
                            and len(
                                [a for a in adjt[j] if a != (i, -abstau)]) >= p
                            and i in all_context_nodes):
                        pairs.append((i, j, abstau))
            return pairs
        elif self.mode == "dummy_search":
            # during discovery of dummy-system links we are only
            # interested in dummy-system pairs
            N = graph.shape[0]
            pairs = []
            for (i, j) in itertools.product(range(N), range(N)):
                for abstau in range(tau_min, tau_max + 1):
                    if (graph[i, j, abstau] != ""
                            and len(
                                [a for a in adjt[j] if a != (i, -abstau)]) >= p
                            and i in self.time_dummy + self.space_dummy):
                        pairs.append((i, j, abstau))
            return pairs
        else:
            return super()._remaining_pairs(graph, adjt, tau_min, tau_max, p)

    def _run_pcalg_test(self, graph, i, abstau, j, S, lagged_parents, max_conds_py,
                        max_conds_px, max_conds_px_lagged, tau_max, alpha_or_thres=None):
        """MCI conditional independence tests within PCMCIplus or PC algorithm. Depending on the J_PCMCIplus step
        the setup is adapted slightly. During the discovery of dummy-system links (step 2) we are using
        the dummy_ci_test and condition on the parents found during step 1; during the discovery of system-system links
        (step 3) we are conditioning on the found contextual parents.
        Parameters
        ----------
        graph : array
            ...
        i : int
            Variable index.
        abstau : int
            Time lag (absolute value).
        j : int
            Variable index.
        S : list
            List of contemporaneous conditions.
        lagged_parents : dictionary of lists
            Dictionary of lagged parents for each node.
        max_conds_py : int
            Max number of lagged parents for node j.
        max_conds_px : int
            Max number of lagged parents for lagged node i.
        max_conds_px_lagged : int
            Maximum number of lagged conditions of X when X is lagged in MCI
            tests. If None is passed, this number is equal to max_conds_px.
        tau_max : int
            Maximum time lag.
        Returns
        -------
        val : float
            Test statistic value.
        pval : float
            Test statistic p-value.
        Z : list
            List of conditions.
        """
        if self.mode == 'dummy_search':
            # Perform independence test adding lagged parents (here lagged_parents might also include
            # (possibly contemp) context parents)
            if lagged_parents is not None:
                conds_y = lagged_parents[j][:max_conds_py]
                # Get the conditions for node i
                if abstau == 0:
                    conds_x = lagged_parents[i][:max_conds_px]
                else:
                    if max_conds_px_lagged is None:
                        conds_x = lagged_parents[i][:max_conds_px]
                    else:
                        conds_x = lagged_parents[i][:max_conds_px_lagged]

            else:
                conds_y = conds_x = []
            # Shift the conditions for X by tau
            conds_x_lagged = [(k, -abstau + k_tau) for k, k_tau in conds_x]

            Z = [node for node in S]
            Z += [node for node in conds_y if
                  node != (i, -abstau) and node not in Z]
            # Remove overlapping nodes between conds_x_lagged and conds_y
            Z += [node for node in conds_x_lagged if node not in Z]

            # during discovery of dummy-system links we are using the dummy_ci_test and condition on the found
            # contextual parents from step 1.
            context_parents_j = self.observed_context_parents[j]
            Z = Z + context_parents_j
            Z = list(dict.fromkeys(Z))  # remove overlaps

            # If middle mark is '-', then set pval=0
            if graph[i, j, abstau] != "" and graph[i, j, abstau][1] == '-':
                val = 1.
                pval = 0.
                dependent = True
                return val, pval, Z, dependent
            else:
                try:
                    val, pval, dependent = self.run_test_dummy([(j, -abstau)], [(i, 0)], Z, tau_max,
                                                               alpha_or_thres=alpha_or_thres)
                    return val, pval, Z, dependent
                except ValueError:
                    val, pval = self.run_test_dummy([(j, -abstau)], [(i, 0)], Z, tau_max, alpha_or_thres=alpha_or_thres)
                    return val, pval, Z

        elif self.mode == 'system_search':
            # during discovery of system-system links we are conditioning on the found contextual parents

            context_parents_j = self.dummy_parents[j] + self.observed_context_parents[j]
            cond = list(S) + context_parents_j
            cond = list(dict.fromkeys(cond))  # remove overlapps
            return super()._run_pcalg_test(graph, i, abstau, j, cond, lagged_parents, max_conds_py,
                                           max_conds_px, max_conds_px_lagged, tau_max, alpha_or_thres)
        else:
            return super()._run_pcalg_test(graph, i, abstau, j, S, lagged_parents, max_conds_py,
                                           max_conds_px, max_conds_px_lagged, tau_max, alpha_or_thres)

    def construct_array_without_zero_rows(self, X, Y, Z=None, tau_max=0, cut_off='2xtau_max', verbosity=0):

        array, xyz, XYZ, data_type = self.dummy_ci_test.dataframe.construct_array(X=X, Y=Y, Z=Z,
                                                                                  tau_max=tau_max,
                                                                                  mask_type=self.dummy_ci_test.mask_type,
                                                                                  return_cleaned_xyz=True,
                                                                                  do_checks=True,
                                                                                  remove_overlaps=True,
                                                                                  cut_off=cut_off,
                                                                                  verbosity=verbosity)
        # remove the parts of the array within dummy that are constant zero (ones are cut off)
        mask = np.all(array == 0., axis=1) | np.all(array == 1., axis=1)
        xyz = xyz[~mask]
        array = array[~mask]
        X, Y, Z = XYZ
        X = [el for i, el in enumerate(X) if ~mask[i]]
        Y = [el for i, el in enumerate(Y) if ~mask[len(X) + i]]
        Z = [el for i, el in enumerate(Z) if ~mask[len(X) + len(Y) + i]]
        return array, xyz, (X, Y, Z), data_type

    def run_test_dummy(self, X, Y, Z=None, tau_max=0, cut_off='2xtau_max', alpha_or_thres=None):
        """Helper function to deal with difficulties in constructing the array for CI testing
        that arise due to the one-hot encoding of the dummy."""
        # Get the array to test on

        if self.dummy_ci_test.measure == "oracle_ci":
            return self.dummy_ci_test.run_test(X, Y, Z=Z, tau_max=tau_max, cut_off=cut_off,
                                               alpha_or_thres=alpha_or_thres)
        else:
            self.dummy_ci_test.dataframe = self.dataframe
            self.dummy_ci_test._get_array = self.construct_array_without_zero_rows
            return self.dummy_ci_test.run_test(X, Y, Z=Z, tau_max=tau_max, cut_off=cut_off,
                                               alpha_or_thres=alpha_or_thres)
