"""Code that runs on the cluster.

helper.py is copied to the cluster and run there with the system python3, so
it uses only the standard library and must keep working with old Python 3
versions (3.6 is the floor: the oldest found on clusters still in service).
Nothing in it may import from visual_workflows.
"""
