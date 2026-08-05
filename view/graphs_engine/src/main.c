// Copyright 2026 Google LLC
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
//     https://www.apache.org/licenses/LICENSE-2.0
//
// Unless required by applicable law or agreed to in writing, software
// distributed under the License is distributed on an "AS IS" BASIS,
// WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
// See the License for the specific language governing permissions and
// limitations under the License.

#ifdef __wasm_simd128__
#include <wasm_simd128.h>
#endif

#ifdef WASM
#define sqrtf __builtin_sqrtf
#else
#include <math.h>
#endif

#ifdef __wasm_simd128__
static inline float wasm_f32x4_hsum(v128_t v) {
    v = wasm_f32x4_add(v, wasm_i32x4_shuffle(v, v, 1, 0, 3, 2));
    v = wasm_f32x4_add(v, wasm_i32x4_shuffle(v, v, 2, 3, 0, 1));
    return wasm_f32x4_extract_lane(v, 0);
}
#endif

#ifdef WASM
    #define WASM_EXPORT(name) __attribute__((export_name(name)))
#else
    #define WASM_EXPORT(name)
#endif

void * alloc(int size);

#ifdef WASM
#define PAGE_SIZE   0x10000
extern unsigned char __heap_base;
static int _heap_end = (int)&__heap_base;
typedef unsigned long long uint64_t;

WASM_EXPORT("alloc")
void * alloc(int size) {
    int ptr = (_heap_end+0xf) & ~0xf; // round up to 16 byte alignment
    _heap_end = ptr + size;
    int pages_needed = (_heap_end+PAGE_SIZE-1) / PAGE_SIZE;
    int pages_n = __builtin_wasm_memory_size(0);
    if (pages_n<pages_needed) {
        __builtin_wasm_memory_grow(0, pages_needed-pages_n);
    }
    return (void *)ptr;
}
#endif

#define BUFFER(name, type, size) type name[size] __attribute__((aligned(16))); \
  WASM_EXPORT("_get_"#name) type* get_##name() {return name;} \
  WASM_EXPORT("_len_"#name"__"#type) int get_##name##_len() {return (size);}

#define DYNAMIC_BUFFER(name, type) type * name = 0; int name##_len = 0; \
  WASM_EXPORT("_get_"#name) type* get_##name() {return name;} \
  WASM_EXPORT("_len_"#name"__"#type) int get_##name##_len() {return (name##_len);} \
  void name##_alloc(int size) {name = (type*)alloc(size*sizeof(type)); name##_len = size;}


#ifdef __wasm_simd128__
typedef v128_t simd_float_t;
#else
typedef float simd_float_t;
#endif

DYNAMIC_BUFFER(sorted_x, simd_float_t);
DYNAMIC_BUFFER(sorted_y, simd_float_t);
DYNAMIC_BUFFER(sorted_z, simd_float_t);
DYNAMIC_BUFFER(force_x, simd_float_t);
DYNAMIC_BUFFER(force_y, simd_float_t);
DYNAMIC_BUFFER(force_z, simd_float_t);

DYNAMIC_BUFFER(node_start, int);
DYNAMIC_BUFFER(node_end, int);
DYNAMIC_BUFFER(node_level, int);
DYNAMIC_BUFFER(node_parent, int);
DYNAMIC_BUFFER(node_next, int);

DYNAMIC_BUFFER(node_center, float);
DYNAMIC_BUFFER(node_min, float);
DYNAMIC_BUFFER(node_max, float);
DYNAMIC_BUFFER(node_mass, float);
DYNAMIC_BUFFER(node_extent, float);
DYNAMIC_BUFFER(node_force, float);

DYNAMIC_BUFFER(points, float);
DYNAMIC_BUFFER(vel, float);
DYNAMIC_BUFFER(links, int);
DYNAMIC_BUFFER(link_weights, float);
DYNAMIC_BUFFER(indices, int);
DYNAMIC_BUFFER(sorted_morton, unsigned int);
DYNAMIC_BUFFER(morton_and_indices, uint64_t);

DYNAMIC_BUFFER(link_force_x, float);
DYNAMIC_BUFFER(link_force_y, float);
DYNAMIC_BUFFER(link_force_z, float);

WASM_EXPORT("init")
void init(int max_point_n, int max_node_n, int max_link_n) {
    // ensure alignment of vector types
    max_point_n = (max_point_n + 3) & ~3;

#ifdef __wasm_simd128__
    sorted_x_alloc(max_point_n/4);
    sorted_y_alloc(max_point_n/4);
    sorted_z_alloc(max_point_n/4);
    force_x_alloc(max_point_n/4);
    force_y_alloc(max_point_n/4);
    force_z_alloc(max_point_n/4);
#else
    sorted_x_alloc(max_point_n);
    sorted_y_alloc(max_point_n);
    sorted_z_alloc(max_point_n);
    force_x_alloc(max_point_n);
    force_y_alloc(max_point_n);
    force_z_alloc(max_point_n);
#endif

    node_start_alloc(max_node_n);
    node_end_alloc(max_node_n);
    node_level_alloc(max_node_n);
    node_parent_alloc(max_node_n);
    node_next_alloc(max_node_n);

    node_center_alloc(max_node_n*3);
    node_min_alloc(max_node_n*3);
    node_max_alloc(max_node_n*3);
    node_mass_alloc(max_node_n);
    node_extent_alloc(max_node_n);
    node_force_alloc(max_node_n*3);

    points_alloc(max_point_n*3);
    vel_alloc(max_point_n*3);
    links_alloc(max_link_n*2);
    link_weights_alloc(max_link_n);
    indices_alloc(max_point_n);
    sorted_morton_alloc(max_point_n);
    morton_and_indices_alloc(max_point_n);
    link_force_x_alloc(max_point_n);
    link_force_y_alloc(max_point_n);
    link_force_z_alloc(max_point_n);
}
BUFFER(tree_center, float, 3);

static float last_tree_extent = 0.0f;
WASM_EXPORT("get_tree_extent") float get_tree_extent() {return last_tree_extent;}

static float global_theta2 = 0.81f;
WASM_EXPORT("setTheta") void setTheta(float theta) { global_theta2 = theta * theta; }

static inline unsigned int dilate3(unsigned int x) {
    x &= 0x3ff;
    x = (x | (x << 16)) & 0x30000ff;
    x = (x | (x << 8))  & 0x300f00f;
    x = (x | (x << 4))  & 0x30c30c3;
    x = (x | (x << 2))  & 0x9249249;
    return x;
}

static void quicksort_u64(unsigned long long* arr, int left, int right) {
    if (left >= right) return;
    unsigned long long pivot = arr[(left + right) / 2];
    int i = left, j = right;
    while (i <= j) {
        while (arr[i] < pivot) i++;
        while (arr[j] > pivot) j--;
        if (i <= j) {
            unsigned long long tmp = arr[i];
            arr[i] = arr[j];
            arr[j] = tmp;
            i++; j--;
        }
    }
    if (left < j) quicksort_u64(arr, left, j);
    if (i < right) quicksort_u64(arr, i, right);
}

static int _leafSize = 16;
static int _maxLevel = 10;
static int _nodeCount = 0;

static void _buildNode(int level, int start, int end, int parentIdx) {
    int ni = _nodeCount++;
    node_start[ni] = start;
    node_end[ni] = end;
    node_level[ni] = level;
    node_parent[ni] = parentIdx;

    if (end - start <= _leafSize || level >= _maxLevel) {
        node_next[ni] = _nodeCount;
        return;
    }

    int count[8] = {0,0,0,0,0,0,0,0};
    int shift = (_maxLevel - level - 1) * 3;
    for (int i = start; i < end; ++i) {
        int octant = (sorted_morton[i] >> shift) & 7;
        count[octant]++;
    }

    for (int i = 0; i < 8; i++) {
        if (count[i]) {
            _buildNode(level + 1, start, start + count[i], ni);
            start += count[i];
        }
    }
    node_next[ni] = _nodeCount;
}

static void accumulateTree(int nodeN) {
    for (int i = 0; i < nodeN * 3; ++i) {
        node_center[i] = 0.0f;
        node_min[i] = 1e30f;
        node_max[i] = -1e30f;
    }
    for (int ni = nodeN - 1; ni >= 0; --ni) {
        if (node_next[ni] == ni + 1) { // leaf
            for (int i = node_start[ni]; i < node_end[ni]; ++i) {
                float px = ((float*)sorted_x)[i];
                float py = ((float*)sorted_y)[i];
                float pz = ((float*)sorted_z)[i];
                node_center[ni * 3]     += px;
                node_center[ni * 3 + 1] += py;
                node_center[ni * 3 + 2] += pz;
                
                if (px < node_min[ni * 3]) node_min[ni * 3] = px;
                if (px > node_max[ni * 3]) node_max[ni * 3] = px;
                if (py < node_min[ni * 3 + 1]) node_min[ni * 3 + 1] = py;
                if (py > node_max[ni * 3 + 1]) node_max[ni * 3 + 1] = py;
                if (pz < node_min[ni * 3 + 2]) node_min[ni * 3 + 2] = pz;
                if (pz > node_max[ni * 3 + 2]) node_max[ni * 3 + 2] = pz;
            }
        } 
        int parent = node_parent[ni];
        if (parent == ni) continue;
        node_center[parent * 3]     += node_center[ni * 3];
        node_center[parent * 3 + 1] += node_center[ni * 3 + 1];
        node_center[parent * 3 + 2] += node_center[ni * 3 + 2];
        
        if (node_min[ni * 3]     < node_min[parent * 3])     node_min[parent * 3]     = node_min[ni * 3];
        if (node_max[ni * 3]     > node_max[parent * 3])     node_max[parent * 3]     = node_max[ni * 3];
        if (node_min[ni * 3 + 1] < node_min[parent * 3 + 1]) node_min[parent * 3 + 1] = node_min[ni * 3 + 1];
        if (node_max[ni * 3 + 1] > node_max[parent * 3 + 1]) node_max[parent * 3 + 1] = node_max[ni * 3 + 1];
        if (node_min[ni * 3 + 2] < node_min[parent * 3 + 2]) node_min[parent * 3 + 2] = node_min[ni * 3 + 2];
        if (node_max[ni * 3 + 2] > node_max[parent * 3 + 2]) node_max[parent * 3 + 2] = node_max[ni * 3 + 2];
    }
    for (int i = 0; i < nodeN; ++i) {
        const float mass = (float)(node_end[i] - node_start[i]);
        node_mass[i] = mass;
        node_center[i * 3]     /= mass;
        node_center[i * 3 + 1] /= mass;
        node_center[i * 3 + 2] /= mass;
        
        float dx = node_max[i * 3] - node_min[i * 3];
        float dy = node_max[i * 3 + 1] - node_min[i * 3 + 1];
        float dz = node_max[i * 3 + 2] - node_min[i * 3 + 2];
        float max_d = dx;
        if (dy > max_d) max_d = dy;
        if (dz > max_d) max_d = dz;
        node_extent[i] = max_d;
    }
}

WASM_EXPORT("buildOctree")
int buildOctree(int pointN, int leafSize, int maxLevel) {
    if (pointN <= 0) return 0;
    _leafSize = leafSize;
    _maxLevel = maxLevel;
    _nodeCount = 0;

    float minX = 1e30f, minY = 1e30f, minZ = 1e30f;
    float maxX = -1e30f, maxY = -1e30f, maxZ = -1e30f;
    for (int i = 0; i < pointN; i++) {
        float x = points[i * 3], y = points[i * 3 + 1], z = points[i * 3 + 2];
        if (x < minX) minX = x; if (x > maxX) maxX = x;
        if (y < minY) minY = y; if (y > maxY) maxY = y;
        if (z < minZ) minZ = z; if (z > maxZ) maxZ = z;
    }

    float dx = maxX - minX, dy = maxY - minY, dz = maxZ - minZ;
    float extent = dx;
    if (dy > extent) extent = dy;
    if (dz > extent) extent = dz;
    last_tree_extent = extent;

    float centerX = (minX + maxX) * 0.5f;
    float centerY = (minY + maxY) * 0.5f;
    float centerZ = (minZ + maxZ) * 0.5f;
    tree_center[0] = centerX; tree_center[1] = centerY; tree_center[2] = centerZ;

    float loX = centerX - extent * 0.5f;
    float loY = centerY - extent * 0.5f;
    float loZ = centerZ - extent * 0.5f;

    float scale = 1023.0f / (extent + 1e-8f);

    for (int i = 0; i < pointN; i++) {
        unsigned int ix = (unsigned int)((points[i * 3] - loX) * scale);
        unsigned int iy = (unsigned int)((points[i * 3 + 1] - loY) * scale);
        unsigned int iz = (unsigned int)((points[i * 3 + 2] - loZ) * scale);
        unsigned int code = dilate3(ix) | (dilate3(iy) << 1) | (dilate3(iz) << 2);
        morton_and_indices[i] = ((unsigned long long)code << 32) | i;
    }

    quicksort_u64(morton_and_indices, 0, pointN - 1);

    for (int i = 0; i < pointN; i++) {
        unsigned int code = (unsigned int)(morton_and_indices[i] >> 32);
        int idx = (int)(morton_and_indices[i] & 0xFFFFFFFF);
        sorted_morton[i] = code;
        indices[i] = idx;
        ((float*)sorted_x)[i] = points[idx * 3];
        ((float*)sorted_y)[i] = points[idx * 3 + 1];
        ((float*)sorted_z)[i] = points[idx * 3 + 2];
    }

    _buildNode(0, 0, pointN, 0);

    accumulateTree(_nodeCount);

    return _nodeCount;
}


WASM_EXPORT("linkForce")
void linkForce(int linkN, float linkStrength, float linkDistance, int pointN) {
    for (int i = 0; i < pointN; i++) {
        link_force_x[i] = 0.0f;
        link_force_y[i] = 0.0f;
        link_force_z[i] = 0.0f;
    }

    for (int p = 0; p < linkN * 2; p += 2) {
        int i = links[p], j = links[p+1];
        float dx = points[j*3]   + vel[j*3]   - points[i*3]   - vel[i*3];
        float dy = points[j*3+1] + vel[j*3+1] - points[i*3+1] - vel[i*3+1];
        float dz = points[j*3+2] + vel[j*3+2] - points[i*3+2] - vel[i*3+2];
        float l2 = dx*dx + dy*dy + dz*dz;
        if (l2 < 1.0f) l2 = 1.0f;
        float l = sqrtf(l2);
        float s = (l - linkDistance) / l * linkStrength * link_weights[p / 2];
        if (s > 0.25f) s = 0.25f;
        else if (s < -0.25f) s = -0.25f;
        dx *= s; dy *= s; dz *= s;
        link_force_x[j] -= dx;
        link_force_y[j] -= dy;
        link_force_z[j] -= dz;
        link_force_x[i] += dx;
        link_force_y[i] += dy;
        link_force_z[i] += dz;
    }
}

int enable_linear_compensation = 0;

WASM_EXPORT("setLinearCompensation")
void setLinearCompensation(int enabled) {
    enable_linear_compensation = enabled;
}

static void compensateVelocity(int pointN) {
    if (pointN <= 0) return;

    // 1. Calculate Center of Mass (CoM) and Mean Velocity
    float sum_px = 0.0f, sum_py = 0.0f, sum_pz = 0.0f;
    float sum_vx = 0.0f, sum_vy = 0.0f, sum_vz = 0.0f;
    for (int i = 0; i < pointN; i++) {
        sum_px += points[i * 3];
        sum_py += points[i * 3 + 1];
        sum_pz += points[i * 3 + 2];
        sum_vx += vel[i * 3];
        sum_vy += vel[i * 3 + 1];
        sum_vz += vel[i * 3 + 2];
    }
    float cx = sum_px / pointN;
    float cy = sum_py / pointN;
    float cz = sum_pz / pointN;
    float mean_vx = sum_vx / pointN;
    float mean_vy = sum_vy / pointN;
    float mean_vz = sum_vz / pointN;

    // 2. Calculate Angular Momentum (L) and Inertia Tensor (I) in CoM frame
    float Lx = 0.0f, Ly = 0.0f, Lz = 0.0f;
    float Ixx = 0.0f, Iyy = 0.0f, Izz = 0.0f;
    float Ixy = 0.0f, Ixz = 0.0f, Iyz = 0.0f;

    for (int i = 0; i < pointN; i++) {
        float x = points[i * 3] - cx;
        float y = points[i * 3 + 1] - cy;
        float z = points[i * 3 + 2] - cz;

        float vx = vel[i * 3] - mean_vx;
        float vy = vel[i * 3 + 1] - mean_vy;
        float vz = vel[i * 3 + 2] - mean_vz;

        Lx += y * vz - z * vy;
        Ly += z * vx - x * vz;
        Lz += x * vy - y * vx;

        Ixx += y * y + z * z;
        Iyy += x * x + z * z;
        Izz += x * x + y * y;
        Ixy -= x * y;
        Ixz -= x * z;
        Iyz -= y * z;
    }

    // 3. Invert Inertia Tensor to solve for angular velocity: omega = I^-1 * L
    float det = Ixx * (Iyy * Izz - Iyz * Iyz) -
                Ixy * (Ixy * Izz - Ixz * Iyz) +
                Ixz * (Ixy * Iyz - Ixz * Iyy);

    float omx = 0.0f, omy = 0.0f, omz = 0.0f;
    if (det > 1e-6f || det < -1e-6f) {
        float invDet = 1.0f / det;
        float Axx = (Iyy * Izz - Iyz * Iyz) * invDet;
        float Ayy = (Ixx * Izz - Ixz * Ixz) * invDet;
        float Azz = (Ixx * Iyy - Ixy * Ixy) * invDet;
        float Axy = (Ixz * Iyz - Ixy * Izz) * invDet;
        float Axz = (Ixy * Iyz - Ixz * Iyy) * invDet;
        float Ayz = (Ixy * Ixz - Iyz * Ixx) * invDet;

        omx = Axx * Lx + Axy * Ly + Axz * Lz;
        omy = Axy * Lx + Ayy * Ly + Ayz * Lz;
        omz = Axz * Lx + Ayz * Ly + Azz * Lz;
    }

    // 4. Subtract linear and rotational velocity components from each velocity
    for (int i = 0; i < pointN; i++) {
        float x = points[i * 3] - cx;
        float y = points[i * 3 + 1] - cy;
        float z = points[i * 3 + 2] - cz;

        vel[i * 3]     -= (omy * z - omz * y);
        vel[i * 3 + 1] -= (omz * x - omx * z);
        vel[i * 3 + 2] -= (omx * y - omy * x);
        if (enable_linear_compensation) {
            vel[i * 3]     -= mean_vx;
            vel[i * 3 + 1] -= mean_vy;
            vel[i * 3 + 2] -= mean_vz;
        }
    }
}

float max_speed = 1.0f;

WASM_EXPORT("setMaxSpeed")
void setMaxSpeed(float s) {
    max_speed = s;
}

WASM_EXPORT("updateNodes")
void updateNodes(int pointN, float velocityDecay) {
    if (pointN <= 0) return;

    // 0. Apply accumulated link forces
    for (int i = 0; i < pointN; i++) {
        vel[i * 3]     += link_force_x[i];
        vel[i * 3 + 1] += link_force_y[i];
        vel[i * 3 + 2] += link_force_z[i];
    }

    compensateVelocity(pointN);

    // 2. Update positions, clamp velocities, and apply decay
    for (int i = 0; i < pointN; i++) {
        float vx = vel[i * 3];
        float vy = vel[i * 3 + 1];
        float vz = vel[i * 3 + 2];
        
        float speed = sqrtf(vx * vx + vy * vy + vz * vz);
        if (speed > max_speed) {
            float scale = max_speed / speed;
            vx *= scale;
            vy *= scale;
            vz *= scale;
            vel[i * 3] = vx;
            vel[i * 3 + 1] = vy;
            vel[i * 3 + 2] = vz;
        }

        points[i * 3]     += vx;
        points[i * 3 + 1] += vy;
        points[i * 3 + 2] += vz;

        vel[i * 3]     *= velocityDecay;
        vel[i * 3 + 1] *= velocityDecay;
        vel[i * 3 + 2] *= velocityDecay;
    }
}

WASM_EXPORT("applyChargeForces")
void applyChargeForces(int pointN, float strength) {
    for (int i = 0; i < pointN; i++) {
        int k = indices[i];
        vel[k * 3]     += strength * ((float*)force_x)[i];
        vel[k * 3 + 1] += strength * ((float*)force_y)[i];
        vel[k * 3 + 2] += strength * ((float*)force_z)[i];
    }
}


WASM_EXPORT("calcMultibodyForce")
void calcMultibodyForce(int pointN, int nodeN, float maxDist) {
    const float theta2 = global_theta2; // Barnes-Hut theta squared
    const float maxDist2 = maxDist * maxDist;
    const float softening = 0.05f;
    const float min_c = 1.0f / (softening + maxDist2);

    for (int pointI = 0; pointI < pointN; ++pointI) {
        const float x = ((float*)sorted_x)[pointI];
        const float y = ((float*)sorted_y)[pointI];
        const float z = ((float*)sorted_z)[pointI];
        float fx = 0.0f, fy = 0.0f, fz = 0.0f;

        for (int nodeI = 0; nodeI < nodeN;) {
            float dx = node_center[nodeI * 3] - x;
            float dy = node_center[nodeI * 3 + 1] - y;
            float dz = node_center[nodeI * 3 + 2] - z;
            float l2 = dx * dx + dy * dy + dz * dz;
            const float w = node_extent[nodeI];

            if (w * w < theta2 * l2) { // Far enough, treat as a single body (Barnes-Hut approximation)
                if (l2 < maxDist2) { // but not too far
                    const float mass = (float)(node_end[nodeI] - node_start[nodeI]); // mass is number of points
                    const float c = mass * (1.0f / (softening + l2) - min_c); // force magnitude
                    fx += c*dx; fy += c*dy; fz += c*dz;
                }
                nodeI = node_next[nodeI]; // Skip to next sibling or ancestor's sibling
            } else { // Too close, traverse children or individual points
                if (node_next[nodeI] == nodeI + 1 && l2 < maxDist2) { // It's a leaf node and not too far
                    int i = node_start[nodeI];
                    int end = node_end[nodeI];

#ifdef __wasm_simd128__
                    v128_t qx = wasm_f32x4_splat(x);
                    v128_t qy = wasm_f32x4_splat(y);
                    v128_t qz = wasm_f32x4_splat(z);
                    v128_t max_d2 = wasm_f32x4_splat(maxDist2);
                    v128_t ones = wasm_f32x4_splat(1.0f);
                    v128_t softening_v = wasm_f32x4_splat(softening);
                    v128_t min_c_v = wasm_f32x4_splat(min_c);

                    v128_t fx_acc = wasm_f32x4_splat(0.0f);
                    v128_t fy_acc = wasm_f32x4_splat(0.0f);
                    v128_t fz_acc = wasm_f32x4_splat(0.0f);

                    for (; i <= end - 4; i += 4) {
                        v128_t px = wasm_v128_load(&((float*)sorted_x)[i]);
                        v128_t py = wasm_v128_load(&((float*)sorted_y)[i]);
                        v128_t pz = wasm_v128_load(&((float*)sorted_z)[i]);

                        v128_t dx_v = wasm_f32x4_sub(px, qx);
                        v128_t dy_v = wasm_f32x4_sub(py, qy);
                        v128_t dz_v = wasm_f32x4_sub(pz, qz);

                        v128_t l2_v = wasm_f32x4_mul(dx_v, dx_v);
                        l2_v = wasm_f32x4_add(l2_v, wasm_f32x4_mul(dy_v, dy_v));
                        l2_v = wasm_f32x4_add(l2_v, wasm_f32x4_mul(dz_v, dz_v));

                        v128_t mask = wasm_f32x4_lt(l2_v, max_d2);
                        v128_t denom = wasm_f32x4_add(softening_v, l2_v);
                        v128_t inv_denom = wasm_f32x4_div(ones, denom);
                        v128_t c_v = wasm_f32x4_sub(inv_denom, min_c_v);

                        c_v = wasm_v128_and(c_v, mask);

                        fx_acc = wasm_f32x4_add(fx_acc, wasm_f32x4_mul(c_v, dx_v));
                        fy_acc = wasm_f32x4_add(fy_acc, wasm_f32x4_mul(c_v, dy_v));
                        fz_acc = wasm_f32x4_add(fz_acc, wasm_f32x4_mul(c_v, dz_v));
                    }

                    fx += wasm_f32x4_hsum(fx_acc);
                    fy += wasm_f32x4_hsum(fy_acc);
                    fz += wasm_f32x4_hsum(fz_acc);
#endif
                    for (; i < end; ++i) {
                        if (i == pointI) continue;
                        float p_dx = ((float*)sorted_x)[i] - x;
                        float p_dy = ((float*)sorted_y)[i] - y;
                        float p_dz = ((float*)sorted_z)[i] - z;
                        float p_l2 = p_dx * p_dx + p_dy * p_dy + p_dz * p_dz;

                        if (p_l2 < maxDist2) {
                            const float c = 1.0f / (softening + p_l2) - min_c;
                            fx += c * p_dx; fy += c * p_dy; fz += c * p_dz;
                        }
                    }
                }
                ++nodeI;
            }
        }
        ((float*)force_x)[pointI] = fx;
        ((float*)force_y)[pointI] = fy;
        ((float*)force_z)[pointI] = fz;
    }
}

