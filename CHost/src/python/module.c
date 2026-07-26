#define PY_SSIZE_T_CLEAN
#include <Python.h>

#include "openrdkc/openrdkc.h"

typedef struct {
    PyObject_HEAD
    ordkc_runtime_t *runtime;
} PyOrdkcRuntime;

static PyObject *raise_native_error(ordkc_result_t result)
{
    PyErr_Format(
        PyExc_RuntimeError,
        "openrdkC native runtime error: %s",
        ordkc_result_name(result));
    return NULL;
}

static PyObject *runtime_new(
    PyTypeObject *type,
    PyObject *args,
    PyObject *kwargs)
{
    PyOrdkcRuntime *self;
    ordkc_result_t result;

    (void)args;
    (void)kwargs;
    self = (PyOrdkcRuntime *)type->tp_alloc(type, 0);
    if (self == NULL) {
        return NULL;
    }
    self->runtime = NULL;
    result = ordkc_runtime_create(&self->runtime);
    if (result != ORDKC_OK) {
        Py_DECREF(self);
        return raise_native_error(result);
    }
    return (PyObject *)self;
}

static void runtime_dealloc(PyOrdkcRuntime *self)
{
    ordkc_runtime_destroy(self->runtime);
    self->runtime = NULL;
    Py_TYPE(self)->tp_free((PyObject *)self);
}

static PyObject *runtime_start(
    PyOrdkcRuntime *self,
    PyObject *Py_UNUSED(ignored))
{
    ordkc_result_t result = ordkc_runtime_start(self->runtime);
    if (result == ORDKC_ERR_ALREADY_RUNNING) {
        Py_INCREF(self);
        return (PyObject *)self;
    }
    if (result != ORDKC_OK) {
        return raise_native_error(result);
    }
    Py_INCREF(self);
    return (PyObject *)self;
}

static PyObject *runtime_stop(
    PyOrdkcRuntime *self,
    PyObject *Py_UNUSED(ignored))
{
    ordkc_result_t result = ordkc_runtime_stop(self->runtime);
    if (result != ORDKC_OK && result != ORDKC_ERR_NOT_RUNNING) {
        return raise_native_error(result);
    }
    Py_RETURN_NONE;
}

static PyObject *runtime_get_is_running(
    PyOrdkcRuntime *self,
    void *Py_UNUSED(closure))
{
    return PyBool_FromLong(ordkc_runtime_is_running(self->runtime) ? 1L : 0L);
}

static PyMethodDef runtime_methods[] = {
    {"start", (PyCFunction)runtime_start, METH_NOARGS,
     PyDoc_STR("Start the lifecycle-only native runtime scaffold.")},
    {"stop", (PyCFunction)runtime_stop, METH_NOARGS,
     PyDoc_STR("Stop the lifecycle-only native runtime scaffold.")},
    {NULL, NULL, 0, NULL}
};

static PyGetSetDef runtime_getset[] = {
    {"is_running", (getter)runtime_get_is_running, NULL,
     PyDoc_STR("Whether the native lifecycle scaffold is running."), NULL},
    {NULL, NULL, NULL, NULL, NULL}
};

static PyType_Slot runtime_slots[] = {
    {Py_tp_new, runtime_new},
    {Py_tp_dealloc, runtime_dealloc},
    {Py_tp_methods, runtime_methods},
    {Py_tp_getset, runtime_getset},
    {0, NULL}
};

static PyType_Spec runtime_spec = {
    .name = "_openrdkC.RuntimeHandle",
    .basicsize = sizeof(PyOrdkcRuntime),
    .itemsize = 0,
    .flags = Py_TPFLAGS_DEFAULT,
    .slots = runtime_slots,
};

static PyObject *native_version(
    PyObject *self,
    PyObject *Py_UNUSED(args))
{
    (void)self;
    return PyUnicode_FromString(ordkc_version());
}

static PyMethodDef module_methods[] = {
    {"native_version", native_version, METH_NOARGS,
     PyDoc_STR("Return the native core version.")},
    {NULL, NULL, 0, NULL}
};

static struct PyModuleDef module_definition = {
    PyModuleDef_HEAD_INIT,
    .m_name = "_openrdkC",
    .m_doc = "Native core for the optional openrdkC runtime.",
    .m_size = -1,
    .m_methods = module_methods,
};

PyMODINIT_FUNC PyInit__openrdkC(void)
{
    PyObject *module;
    PyObject *runtime_type;

    module = PyModule_Create(&module_definition);
    if (module == NULL) {
        return NULL;
    }
    runtime_type = PyType_FromSpec(&runtime_spec);
    if (runtime_type == NULL) {
        Py_DECREF(module);
        return NULL;
    }
    if (PyModule_AddObject(module, "RuntimeHandle", runtime_type) < 0) {
        Py_DECREF(runtime_type);
        Py_DECREF(module);
        return NULL;
    }
    if (PyModule_AddStringConstant(module, "__version__", ordkc_version()) < 0) {
        Py_DECREF(module);
        return NULL;
    }
    return module;
}

