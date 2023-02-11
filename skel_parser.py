import subprocess
import tempfile

from cloudvolume import Skeleton
import numpy as np
import open3d as o3d

TMP_DIR = "/tmp"
BIN_PATH = "/home/jason/projects/l1/L1-Skeleton/PointCloud/PointCloudL1"
DEFAULT_JSON_CONFIG_PATH = (
    "/home/jason/projects/l1/L1-Skeleton/default_skeleton_config.json"
)


def parse_skel(filename):
    result = {}
    lines = open(filename).readlines()
    lines = [line.strip() for line in lines]
    lines = [line for line in lines if line != ""]

    assert lines[0].startswith("ON")
    num_original = int(lines[0].split()[1])
    lines = lines[1:]
    result["original"] = np.stack(
        [np.array([float(x) for x in line.split()]) for line in lines[:num_original]],
        axis=0,
    )
    lines = lines[num_original:]

    # NOTE: samples are potentially inf
    assert lines[0].startswith("SN")
    num_sampled = int(lines[0].split()[1])
    lines = lines[1:]
    result["sample"] = np.stack(
        [np.array([float(x) for x in line.split()]) for line in lines[:num_sampled]],
        axis=0,
    )
    lines = lines[num_sampled:]

    assert lines[0].startswith("CN")
    num_branches = int(lines[0].split()[1])
    lines = lines[1:]

    branches = []
    for _ in range(num_branches):
        assert lines[0].startswith("CNN")
        num_nodes = int(lines[0].split()[1])
        lines = lines[1:]
        branches.append(
            np.stack(
                [
                    np.array([float(x) for x in line.split()])
                    for line in lines[:num_nodes]
                ],
                axis=0,
            )
        )
        lines = lines[num_nodes:]
    result["branches"] = branches
    len_branches = [x.shape[0] for x in branches]

    assert lines[0] == "EN 0"
    lines = lines[1:]
    assert lines[0] == "BN 0"
    lines = lines[1:]

    assert lines[0].startswith("S_onedge")
    lines = lines[1:]
    result["sample_onedge"] = np.array(list(map(int, lines[0].split()))) > 0
    lines = lines[1:]

    assert lines[0].startswith("GroupID")
    lines = lines[1:]
    result["sample_groupid"] = np.array(list(map(int, lines[0].split())))
    lines = lines[1:]

    # flattened branches
    assert lines[0].startswith("SkelRadius")
    lines = lines[1:]
    result["branches_skelradius"] = np.split(
        np.array(list(map(float, lines[0].split()))), np.cumsum(len_branches)
    )[:-1]
    lines = lines[1:]

    assert lines[0].startswith("Confidence_Sigma")
    lines = lines[1:]
    result["sample_confidence_sigma"] = np.array(list(map(float, lines[0].split())))
    lines = lines[1:]

    assert lines[0] == "SkelRadius2 0"
    lines = lines[1:]
    assert lines[0] == "Alpha 0"
    lines = lines[1:]

    assert lines[0].startswith("Sample_isVirtual")
    lines = lines[1:]
    result["sample_isvirtual"] = np.array(list(map(int, lines[0].split()))) > 0
    lines = lines[1:]

    assert lines[0].startswith("Sample_isBranch")
    lines = lines[1:]
    result["sample_isbranch"] = np.array(list(map(int, lines[0].split()))) > 0
    lines = lines[1:]

    assert lines[0].startswith("Sample_radius")
    lines = lines[2:]

    assert lines[0].startswith("Skel_isVirtual")
    lines = lines[1:]
    result["skel_isvirtual"] = np.split(
        np.array(list(map(int, lines[0].split()))) > 0, np.cumsum(len_branches)
    )[:-1]
    lines = lines[1:]

    # NOTE: this does not generate anything useful, as samples are potentially inf
    assert lines[0].startswith("Corresponding_sample_index")
    lines = lines[1:]
    result["corresponding_sample_index"] = np.split(
        np.array(list(map(int, lines[0].split()))), np.cumsum(len_branches)
    )[:-1]
    lines = lines[1:]

    assert len(lines) == 0

    return result


def to_cloud_volume_skeleton(parsed):
    branch_length = np.array([len(x) for x in parsed["branches"]])
    flattened_vertices = np.concatenate(parsed["branches"])
    flattened_radii = np.concatenate(parsed["branches_skelradius"])

    # NOTE: may need to replace this with np.isclose to allow within epsilon dists
    unique, index, inverse = np.unique(
        flattened_vertices, axis=0, return_index=True, return_inverse=True
    )
    edges = []

    branch_inverse = np.split(inverse, np.cumsum(branch_length))[:-1]
    for branch in branch_inverse:
        edges.append(np.stack([branch[:-1], branch[1:]], axis=1))
    edges = np.concatenate(edges, axis=0)

    flattened_radii = flattened_radii[np.argsort(inverse)]
    radii = np.split(flattened_radii, np.cumsum(branch_length))[:-1]
    assert max([len(np.unique(x)) for x in radii])
    radii = flattened_radii[index]

    skel = Skeleton(vertices=unique, edges=edges, radii=radii)

    return skel


def point_cloud_to_ply(pc, out_filename):
    # NOTE: assumes isotropic data, properly scaled data
    # pc: [N, 3]
    pcd = o3d.geometry.PointCloud()
    pcd.points = o3d.utility.Vector3dVector(pc)
    o3d.io.write_point_cloud(out_filename, pcd)

    return out_filename


def generate_skeleton(pc):
    ply_path = point_cloud_to_ply(
        pc, tempfile.NamedTemporaryFile(suffix=".ply", dir=TMP_DIR, delete=False).name
    )
    skel_path = tempfile.NamedTemporaryFile(
        suffix=".skel", dir=TMP_DIR, delete=False
    ).name
    cmd = f"{BIN_PATH} {ply_path} {skel_path} {DEFAULT_JSON_CONFIG_PATH}"

    log_file = tempfile.NamedTemporaryFile(suffix=".txt", dir=TMP_DIR, delete=False)
    print(f"Running command: {cmd}")
    print(f"Logging to: {log_file.name}")

    # NOTE: this is a blocking call, can use subprocess.Popen to run in background
    subprocess.run(cmd.split(), stdout=log_file, stderr=log_file)

    skel = parse_skel(skel_path)
    skeleton = to_cloud_volume_skeleton(skel)

    return skeleton


if __name__ == "__main__":
    import nibabel as nib

    vol = nib.load("RibFrac1-rib-seg.nii.gz").get_fdata()
    # downscale
    pc = np.argwhere(vol == 1) * 0.01
    skeleton = generate_skeleton(pc)
    skeleton.viewer()
