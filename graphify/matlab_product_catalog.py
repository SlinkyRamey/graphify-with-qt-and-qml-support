"""Reviewed R2026b MathWorks names for conservative offline attribution.

The entries describe documented names and saved library paths. They do not
assert that a product is installed, licensed, or required at runtime.
"""

# Exact spellings are deliberate. Shared or type-dispatched names such as
# isstable, ss, optimoptions and parfevalOnAll need evidence beyond a bare call.
API_PRODUCTS: dict[str, str] = {
    # https://www.mathworks.com/help/matlab/matlab-unit-test-framework.html
    "matlab.lang.makeValidName": "MATLAB",
    "matlab.unittest.fixtures.PathFixture": "MATLAB",
    # https://www.mathworks.com/help/simulink/referencelist.html?type=function
    "Simulink.ID.getFullName": "Simulink",
    "Simulink.ID.getHandle": "Simulink",
    "Simulink.SimulationData.ModelLoggingInfo": "Simulink",
    "Simulink.SimulationData.SignalLoggingInfo": "Simulink",
    "Simulink.SimulationInput": "Simulink",
    "Simulink.data.dictionary.getOpenDictionaryPaths": "Simulink",
    "Simulink.data.dictionary.open": "Simulink",
    "Simulink.fileGenControl": "Simulink",
    "Simulink.getFileChecksum": "Simulink",
    "add_block": "Simulink",
    "add_line": "Simulink",
    "bdIsLoaded": "Simulink",
    "close_system": "Simulink",
    "find_system": "Simulink",
    "get_param": "Simulink",
    "load_system": "Simulink",
    "new_system": "Simulink",
    "open_system": "Simulink",
    "parsim": "Simulink",
    "save_system": "Simulink",
    "set_param": "Simulink",
    # https://www.mathworks.com/help/dsp/ref/dsp.firfilter-system-object.html
    "dsp.FIRFilter": "DSP System Toolbox",
    # https://www.mathworks.com/help/parallel-computing/parpool.html
    "gcp": "Parallel Computing Toolbox",
    "getCurrentTask": "Parallel Computing Toolbox",
    "parpool": "Parallel Computing Toolbox",
    # https://www.mathworks.com/help/control/ref/lti.lqr.html
    # https://www.mathworks.com/help/control/ref/lyap.html
    "lqr": "Control System Toolbox",
    "lyap": "Control System Toolbox",
    # https://www.mathworks.com/help/simulink/trimming-and-linearization.html
    "linio": "Simulink Control Design",
    "slLinearizer": "Simulink Control Design",
    # https://www.mathworks.com/help/optim/ug/problems-handled-by-optimization-toolbox-functions.html
    "coneprog": "Optimization Toolbox",
    "fmincon": "Optimization Toolbox",
    "quadprog": "Optimization Toolbox",
    "secondordercone": "Optimization Toolbox",
    # https://www.mathworks.com/help/symbolic/sym.html
    "sym": "Symbolic Math Toolbox",
    "syms": "Symbolic Math Toolbox",
    # https://www.mathworks.com/help/stateflow/api/stateflow.chart.html
    "Stateflow.Chart": "Stateflow",
    "Stateflow.State": "Stateflow",
    "sfroot": "Stateflow",
    # https://www.mathworks.com/help/robotics/manipulator-modeling.html
    "rigidBody": "Robotics System Toolbox",
    "rigidBodyJoint": "Robotics System Toolbox",
    "rigidBodyTree": "Robotics System Toolbox",
    # https://www.mathworks.com/help/sl3d/sim3d.actor.html
    "sim3d.Actor": "Simulink 3D Animation",
    # https://www.mathworks.com/help/mpc/ref/mpc.html
    "mpc": "Model Predictive Control Toolbox",
    "nlmpc": "Model Predictive Control Toolbox",
}

# Match the full SourceBlock value, including saved line breaks. Some display
# names occur in more than one product browser, while the saved paths retain
# which library supplied the reference.
LIBRARY_PRODUCTS: dict[str, str] = {
    # https://www.mathworks.com/help/simulink/slref/crossproduct.html
    # https://www.mathworks.com/help/simulink/slref/comparetoconstant.html
    # https://www.mathworks.com/help/simulink/ref_extras/matrixinterpolation.html
    "matrix_library/Cross Product": "Simulink",
    "simulink/Logic and Bit\nOperations/Compare\nTo Constant": "Simulink",
    "simulink_extras/Additional\nLookup Tables/Matrix Interpolation": "Simulink",
    # https://www.mathworks.com/help/dsp/ug/linear-algebra-and-least-squares.html
    "dspfactors/QR Factorization": "DSP System Toolbox",
    "dspsolvers/Cholesky Solver": "DSP System Toolbox",
    "dspsolvers/Forward\nSubstitution": "DSP System Toolbox",
    # https://www.mathworks.com/help/robotics/ref/getjacobian.html
    # https://www.mathworks.com/help/robotics/ref/gettransformblock.html
    "robotmaniplib/Get Jacobian": "Robotics System Toolbox",
    "robotmaniplib/Get Transform": "Robotics System Toolbox",
    # https://www.mathworks.com/help/sl3d/simulation3dactor.html
    # https://www.mathworks.com/help/sl3d/simulation3dsceneconfiguration.html
    # https://www.mathworks.com/help/vdynblks/ref/simulation3dcameraget.html
    "sim3dlib/Simulation 3D Actor": "Simulink 3D Animation",
    "sim3dlib/Simulation 3D Camera Get": "Simulink 3D Animation",
    "sim3dlib/Simulation 3D Scene Configuration": "Simulink 3D Animation",
}

# Native product analysis reports multiple possible owners for the Actor API,
# and the reviewed R2026b sim3dlib is installed in a shared product directory.
SHARED_PRODUCT_SYMBOLS = frozenset({
    "sim3d.Actor",
    "sim3dlib/Simulation 3D Actor",
    "sim3dlib/Simulation 3D Camera Get",
    "sim3dlib/Simulation 3D Scene Configuration",
})
