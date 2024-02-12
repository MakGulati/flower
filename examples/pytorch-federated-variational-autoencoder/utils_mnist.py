import torch
import torch.nn as nn
from torchsummary import summary
import torch.nn.functional as F
import torchvision.transforms as transforms
from torch.utils.data import DataLoader, Subset, ConcatDataset
from torchvision.datasets import MNIST
import numpy as np
import matplotlib.pyplot as plt
from torchvision.utils import save_image
from collections import OrderedDict
import copy

from flwr.common import parameters_to_ndarrays
from torch.nn.parameter import Parameter
from typing import List, Tuple
from sklearn.mixture import GaussianMixture
import matplotlib

matplotlib.use("Agg")


class Net(nn.Module):
    def __init__(self, h_dim=64, z_dim=10) -> None:
        super(Net, self).__init__()

        # Encoder
        self.encoder = nn.Sequential(
            nn.Linear(28 * 28, h_dim),
            nn.ReLU(),
            # nn.Linear(h_dim, h_dim // 2),
            # nn.ReLU(),
        )

        # Latent space
        self.fc_mu = nn.Linear(h_dim, z_dim)
        self.fc_logvar = nn.Linear(h_dim, z_dim)

        # Decoder
        self.decoder = nn.Sequential(
            nn.Linear(z_dim, h_dim),
            nn.ReLU(),
            # nn.Linear(h_dim // 2, h_dim),
            # nn.ReLU(),
            nn.Linear(h_dim, 28 * 28),
            nn.Sigmoid(),  # Use Sigmoid activation for MNIST (pixel values between 0 and 1)
        )

    def reparametrize(self, h):
        """Reparametrization layer of VAE."""
        mu = self.fc_mu(h)
        logvar = self.fc_logvar(h)
        std = torch.exp(0.5 * logvar)
        eps = torch.randn_like(std)
        z = mu + std * eps
        return z, mu, logvar

    def encode(self, x):
        """Encoder of the VAE."""
        x = x.view(x.size(0), -1)  # Flatten input for fully connected layers
        h = self.encoder(x)
        z, mu, logvar = self.reparametrize(h)
        return z, mu, logvar

    def decode(self, z):
        """Decoder of the VAE."""
        x_recon = self.decoder(z)
        x_recon = x_recon.view(-1, 1, 28, 28)  # Reshape to image dimensions
        return x_recon

    def forward(self, x):
        z, mu, logvar = self.encode(x)
        x_recon = self.decode(z)
        return x_recon, mu, logvar


class VAE(nn.Module):
    def __init__(
        self, x_dim=784, h_dim1=512, h_dim2=256, h_dim3=32, z_dim=2, encoder_only=False
    ):
        super(VAE, self).__init__()
        self.encoder_only = encoder_only
        # encoder part
        self.fc1 = nn.Linear(x_dim, h_dim1)
        self.fc2 = nn.Linear(h_dim1, h_dim2)
        self.fc3 = nn.Linear(h_dim2, h_dim3)
        self.fc42 = nn.Linear(h_dim3, z_dim)
        self.fc41 = nn.Linear(h_dim3, z_dim)
        # decoder part
        self.fc5 = nn.Linear(z_dim, h_dim3)
        self.fc6 = nn.Linear(h_dim3, h_dim2)
        self.fc7 = nn.Linear(h_dim2, h_dim1)
        self.fc8 = nn.Linear(h_dim1, x_dim)

    def encoder(self, x):
        h = F.relu(self.fc1(x))
        h = F.relu(self.fc2(h))
        h = F.relu(self.fc3(h))
        return self.fc41(h), self.fc42(h)  # mu, log_var

    def sampling(self, mu, log_var):
        std = torch.exp(0.5 * log_var)
        eps = torch.randn_like(std)
        return eps.mul(std).add_(mu)  # return z sample

    def decoder(self, z):
        h = F.relu(self.fc5(z))
        h = F.relu(self.fc6(h))
        h = F.relu(self.fc7(h))
        return torch.sigmoid(self.fc8(h))

    def forward(self, x):
        mu, log_var = self.encoder(x.view(-1, 784))
        z = self.sampling(mu, log_var)
        if self.encoder_only:
            output = z
        else:
            output = self.decoder(z)

        return output, mu, log_var


def alignment_dataloader(samples_per_class=100, batch_size=8, shuffle=False):
    # Load the MNIST test dataset
    mnist_test = MNIST(
        root="./mnist_data/",
        train=False,
        download=True,
        transform=transforms.ToTensor(),
    )

    # Create an alignment dataset with 20 samples for each class
    alignment_datasets = []

    for class_label in range(10):
        class_indices = [
            i for i, (img, label) in enumerate(mnist_test) if label == class_label
        ]
        selected_indices = class_indices[:samples_per_class]
        alignment_dataset = Subset(mnist_test, selected_indices)
        alignment_datasets.append(alignment_dataset)

    # Concatenate the alignment datasets into one
    alignment_dataset = ConcatDataset(alignment_datasets)

    # Create a DataLoader for the alignment dataset
    alignment_loader = DataLoader(
        alignment_dataset, batch_size=batch_size, shuffle=shuffle
    )
    return alignment_loader


def subset_alignment_dataloader(samples_per_class=100, batch_size=8, shuffle=True):
    test_dataset = MNIST(
        root="./mnist_data", train=False, download=True, transform=transforms.ToTensor()
    )
    partitions_idx = non_iid_train_iid_test_6789(alignment=True)
    torch.manual_seed(6789)
    alignment_datasets = []
    for partition_idx in partitions_idx:
        selected_points = partition_idx[:samples_per_class]
        alignment_dataset = Subset(test_dataset, selected_points)
        alignment_datasets.append(alignment_dataset)

    # Concatenate the alignment datasets into one
    alignment_dataset = ConcatDataset(alignment_datasets)

    # Create a DataLoader for the alignment dataset
    alignment_loader = DataLoader(
        alignment_dataset, batch_size=batch_size, shuffle=shuffle
    )
    return alignment_loader


def load_data_mnist(normalise=False, batch_size=64):
    """Load MNIST (training and test set)."""
    if normalise:
        transform = transforms.Compose(
            [transforms.ToTensor(), transforms.Normalize((0.5,), (0.5,))]
        )
    else:
        transform = transforms.ToTensor()

    trainset = MNIST(
        root="./mnist_data/", train=True, download=True, transform=transform
    )
    testset = MNIST(
        root="./mnist_data/", train=False, download=True, transform=transform
    )
    trainloader = DataLoader(trainset, batch_size=batch_size, shuffle=True)
    testloader = DataLoader(testset, batch_size=batch_size)
    return trainloader, testloader


def non_iid_train_iid_test():
    # Load the MNIST training dataset
    train_dataset = MNIST(
        root="./mnist_data/", train=True, download=True, transform=transforms.ToTensor()
    )

    # Define class pairs for each partition
    class_partitions = [(0, 1), (2, 3), (4, 5), (6, 7), (8, 9)]

    # Create a list to store datasets for each partition
    partition_datasets_train = []

    # Iterate over class pairs and create a dataset for each partition
    for class_pair in class_partitions:
        class_filter = lambda label: label in class_pair
        filtered_indices = [
            i for i, (_, label) in enumerate(train_dataset) if class_filter(label)
        ]

        # Use Subset to create a dataset with filtered indices
        partition_dataset = torch.utils.data.Subset(train_dataset, filtered_indices)
        partition_datasets_train.append(partition_dataset)

        # Load the MNIST test dataset
    test_dataset = MNIST(
        root="./mnist_data", train=False, download=True, transform=transforms.ToTensor()
    )

    # Specify the size of each partition
    partition_sizes = [len(test_dataset) // 5] * 4 + [
        len(test_dataset) - (len(test_dataset) // 5) * 4
    ]

    # Use random_split to create 5 datasets with random samples
    partition_datasets_test = torch.utils.data.random_split(
        test_dataset, partition_sizes
    )
    return partition_datasets_train, partition_datasets_test


def non_iid_train_iid_test_6789(seed=6789, alignment=False):
    # Load the MNIST training dataset
    torch.manual_seed(seed)

    train_dataset = MNIST(
        root="./mnist_data/", train=True, download=True, transform=transforms.ToTensor()
    )

    # Define class pairs for each partition
    class_partitions = [(6, 7), (8, 9)]

    # Create a list to store datasets for each partition
    partition_datasets_train = []

    # Iterate over class pairs and create a dataset for each partition
    for class_pair in class_partitions:
        class_filter = lambda label: label in class_pair
        filtered_indices = [
            i for i, (_, label) in enumerate(train_dataset) if class_filter(label)
        ]

        # Use Subset to create a dataset with filtered indices
        partition_dataset = Subset(train_dataset, filtered_indices)
        partition_datasets_train.append(partition_dataset)

        # Load the MNIST test dataset
    test_dataset = MNIST(
        root="./mnist_data", train=False, download=True, transform=transforms.ToTensor()
    )
    class_partitions_test = [6, 7, 8, 9]

    partition_datasets_test = []
    partition_datasets_alignment = []

    # Iterate over class pairs and create a dataset for each partition
    for class_pair in class_partitions_test:
        class_filter = class_pair
        filtered_indices = [
            i for i, (_, label) in enumerate(test_dataset) if class_filter == label
        ]

        # Use Subset to create a dataset with filtered indices
        partition_datasets_test.append(Subset(test_dataset, filtered_indices[500:]))
        partition_datasets_alignment.append(filtered_indices[:500])

    if alignment:
        return partition_datasets_alignment
    combined_testset = [ConcatDataset(partition_datasets_test)] * len(
        partition_datasets_train
    )
    return (
        partition_datasets_train,
        combined_testset,
    )


def iid_train_iid_test():
    # Load the MNIST training dataset
    train_dataset = MNIST(
        root="./mnist_data/", train=True, download=True, transform=transforms.ToTensor()
    )

    # Specify the size of each partition
    partition_sizes_train = [len(train_dataset) // 5] * 4 + [
        len(train_dataset) - (len(train_dataset) // 5) * 4
    ]

    # Use random_split to create 5 datasets with random samples
    partition_datasets_train = torch.utils.data.random_split(
        train_dataset, partition_sizes_train
    )
    # Load the MNIST test dataset
    test_dataset = MNIST(
        root="./data", train=False, download=True, transform=transforms.ToTensor()
    )

    # Specify the size of each partition
    partition_sizes_test = [len(test_dataset) // 5] * 4 + [
        len(test_dataset) - (len(test_dataset) // 5) * 4
    ]

    # Use random_split to create 5 datasets with random samples
    partition_datasets_test = torch.utils.data.random_split(
        test_dataset, partition_sizes_test
    )
    return partition_datasets_train, partition_datasets_test


def train(
    net,
    trainloader,
    optimizer,
    config,
    epochs,
    device,
    num_classes=None,
    if_return=False,
):
    """Train the network on the training set."""
    net.train()
    for _ in range(epochs):
        for images, _ in trainloader:
            images = images.to(device)
            optimizer.zero_grad()
            recon_images, mu, logvar = net(images)
            recon_loss = F.binary_cross_entropy(
                recon_images, images.view(-1, 784), reduction="sum"
            )

            kld_loss = -0.5 * torch.sum(1 + logvar - mu.pow(2) - logvar.exp())
            loss = recon_loss + kld_loss * 1
            loss.backward()
            optimizer.step()
    if if_return:
        return net


def train_prox(
    net,
    trainloader,
    optim,
    config,
    epochs,
    device,
    num_classes,
):
    criterion = None  # loss in functional form
    global_params = [val.detach().clone() for val in net.parameters()]
    net.train()
    for _ in range(epochs):
        net, vae_term, prox_term = _train_one_epoch(
            net,
            global_params,
            trainloader,
            device,
            criterion,
            optim,
            config.get("proximal_mu", 1),
        )
    return vae_term, prox_term


def _train_one_epoch(
    net,
    global_params: List[Parameter],
    trainloader: DataLoader,
    device: torch.device,
    criterion,
    optimizer: torch.optim.Adam,
    proximal_mu: float,
) -> nn.Module:
    for images, labels in trainloader:
        images, labels = images.to(device), labels.to(device)
        optimizer.zero_grad()
        proximal_term = 0.0
        for local_weights, global_weights in zip(net.parameters(), global_params):
            proximal_term += torch.square((local_weights - global_weights).norm(2))
        recon_images, mu, logvar = net(images)
        recon_loss = F.binary_cross_entropy(
            recon_images, images.view(-1, 784), reduction="sum"
        )
        kld_loss = -0.5 * torch.mean(1 + logvar - mu.pow(2) - logvar.exp())
        loss = recon_loss + kld_loss + (proximal_mu / 2) * proximal_term

        vae_term = recon_loss + kld_loss
        prox_term = (proximal_mu / 2) * proximal_term
        loss.backward()
        optimizer.step()
    return net, vae_term, prox_term


def vae_loss(recon_img, img, mu, logvar):
    # Reconstruction loss using binary cross-entropy
    condition = (recon_img >= 0) & (recon_img <= 1)
    assert torch.all(condition), "Values should be between 0 and 1"
    recon_loss = F.binary_cross_entropy(
        recon_img, img.view(-1, img.shape[2] * img.shape[3]), reduction="sum"
    )

    # KL divergence loss
    kld_loss = -0.5 * torch.sum(1 + logvar - mu.pow(2) - logvar.exp())

    # Total VAE loss
    total_loss = recon_loss + kld_loss

    return total_loss


def train_align(
    net,
    trainloader,
    optimizer,
    config,
    epochs,
    device,
    num_classes=None,
):
    """Train the network on the training set."""
    net.train()
    temp_gen_model = VAE(encoder_only=True).to(device)
    gen_weights = parameters_to_ndarrays(config["gen_params"])
    params_dict = zip(temp_gen_model.state_dict().keys(), gen_weights)
    state_dict = OrderedDict({k: torch.from_numpy(v) for k, v in params_dict})
    temp_gen_model.load_state_dict(state_dict, strict=True)
    # copied_model = copy.deepcopy(temp_gen_model)

    temp_gen_model.eval()
    # sample_per_class = config.get("sample_per_class", 100)
    sample_per_class = config["sample_per_class"]

    # lambda_reg = config.get("lambda_reg", 0.1)
    lambda_reg = config["lambda_reg"]

    # lambda_align = config.get("lambda_align", 100)
    lambda_align = config["lambda_align"]
    align_loader = subset_alignment_dataloader(
        samples_per_class=sample_per_class, batch_size=sample_per_class * num_classes
    )
    for _ in range(epochs):
        for images, _ in trainloader:
            images = images.to(device)
            optimizer.zero_grad()
            recon_images, mu, logvar = net(images)
            vae_loss1 = vae_loss(recon_images, images, mu, logvar)
            z_g, mu_g, logvar_g = temp_gen_model(images)
            vae_loss2 = vae_loss(net.decoder(z_g), images, mu_g, logvar_g)
            loss = vae_loss1 + lambda_reg * vae_loss2
            for align_img, _ in align_loader:
                align_img = align_img.to(device)
                _, mu_g, log_var_g = temp_gen_model(align_img)
                _, mu, log_var = net(align_img)

                loss_align = 0.5 * (log_var_g - log_var - 1) + (
                    log_var.exp() + (mu - mu_g).pow(2)
                ) / (2 * log_var_g.exp())
            loss_align_reduced = loss_align.sum(dim=1).sum()
            loss += lambda_align * loss_align_reduced
            loss.backward()
            optimizer.step()
    # assert all(
    #     torch.equal(val1, val2)
    #     for (_, val1), (_, val2) in zip(
    #         temp_gen_model.state_dict().items(), copied_model.state_dict().items()
    #     )
    # ), "Not all parameters are equal."
    return (
        vae_loss1.item(),
        lambda_reg * vae_loss2.item(),
        lambda_align * loss_align_reduced.item(),
    )


def test(net, testloader, device, kl_term=0):
    """Validate the network on the entire test set."""
    total, loss = 0, 0.0
    net.eval()
    with torch.no_grad():
        for idx, data in enumerate(testloader):
            images = data[0].to(device)
            recon_images, mu, logvar = net(images)
            recon_loss = F.binary_cross_entropy(
                recon_images, images.view(-1, 784), reduction="sum"
            )
            kld_loss = -0.5 * torch.mean(1 + logvar - mu.pow(2) - logvar.exp())
            loss += recon_loss + kld_loss * kl_term
            total += len(images)
    # TODO: accu=-1*loss
    return (loss.item() / total), -1 * (loss.item() / total)


def eval_reconstrution(net, testloader, device):
    """Validate the network on the entire test set."""
    total, loss = 0, 0.0
    net.eval()
    with torch.no_grad():
        for idx, data in enumerate(testloader):
            images = data[0].to(device)
            recon_images, mu, logvar = net(images)
            recon_loss = F.binary_cross_entropy(
                recon_images, images.view(-1, 784), reduction="sum"
            )
            loss += recon_loss
            total += len(images)
    return loss.item() / total


def visualize_gen_image(net, testloader, device, rnd=None, folder=None):
    """Validate the network on the entire test set."""
    with torch.no_grad():
        for idx, data in enumerate(testloader):
            images = data[0].to(device)
            break
        save_image(images.view(64, 1, 28, 28), f"{folder}/true_img_at_{rnd}.png")

        # Generate image using your generate function
        generated_tensors = generate(net, images)
        generated_img = generated_tensors[0]
        save_image(
            generated_img.view(64, 1, 28, 28), f"{folder}/test_generated_at_{rnd}.png"
        )
        return (
            f"{folder}/true_img_at_{rnd}.png",
            f"{folder}/test_generated_at_{rnd}.png",
        )


def visualize_latent_representation(
    model, test_loader, device, rnd=None, folder=None, use_PCA=False
):
    model.eval()
    all_latents = []
    all_labels = []

    with torch.no_grad():
        for data, labels in test_loader:
            data = data.to(device)
            _, mu, _ = model(data)
            all_latents.append(mu.cpu().numpy())
            all_labels.append(labels.numpy())

    all_latents = np.concatenate(all_latents, axis=0)
    all_labels = np.concatenate(all_labels, axis=0)
    reduced_latents = all_latents
    if use_PCA:
        # Apply PCA using PyTorch
        cov_matrix = torch.tensor(np.cov(all_latents.T), dtype=torch.float32)
        _, _, V = torch.svd_lowrank(cov_matrix, q=2)

        # Project data onto the first two principal components
        reduced_latents = torch.mm(torch.tensor(all_latents, dtype=torch.float32), V)

        # Convert to numpy array
        reduced_latents = reduced_latents.numpy()
    plt.figure(figsize=(10, 8))
    # Visualize latent representation
    scatter = plt.scatter(
        reduced_latents[:, 0], reduced_latents[:, 1], c=all_labels, cmap="tab10"
    )
    plt.colorbar(scatter, label="Digit Label")
    plt.title("Latent Representation Visualization")
    plt.xlabel("Principal Component 1")
    plt.ylabel("Principal Component 2")

    plt.savefig(f"{folder}/latent_rep_at_{rnd}.png")


def visualize_gmm_latent_representation(
    model, test_loader, device, rnd=None, folder=None, use_PCA=False, num_class=10
):
    model.eval()
    all_latents = []
    all_labels = []
    all_means = []

    with torch.no_grad():
        for data, labels in test_loader:
            data = data.to(device)
            z, mu, _ = model(data)
            all_latents.append(z.cpu().numpy())
            all_means.append(mu.cpu().numpy())
            all_labels.append(labels.numpy())

    all_latents = np.concatenate(all_latents, axis=0)
    all_labels = np.concatenate(all_labels, axis=0)
    all_means = np.concatenate(all_means, axis=0)
    reduced_latents = all_latents
    if use_PCA:
        # Apply PCA using PyTorch
        cov_matrix = torch.tensor(np.cov(all_latents.T), dtype=torch.float32)
        _, _, V = torch.svd_lowrank(cov_matrix, q=2)

        # Project data onto the first two principal components
        reduced_latents = torch.mm(torch.tensor(all_latents, dtype=torch.float32), V)

        # Convert to numpy array
        reduced_latents = reduced_latents.numpy()
    fig, ax = plt.subplots(figsize=(8, 8))

    scatter2 = ax.scatter(
        reduced_latents[:, 0],
        reduced_latents[:, 1],
        c=all_labels,
        cmap="Set1",
        label="Labels",
        zorder=2,
    )
    ax.set_title("Latent Representation with True Labels")
    ax.set_xlabel("Principal Component 1")
    ax.set_ylabel("Principal Component 2")
    ax.legend()
    ax.grid()
    # Create a colorbar for the scatter plots
    # cbar1 = plt.colorbar(scatter1, ax=axs[0], label="GMM Predictions")
    cbar2 = plt.colorbar(scatter2, ax=ax, label="True Labels")

    # Set colorbar ticks and labels based on unique label values
    unique_labels = np.unique(all_labels)

    cbar2.set_ticks(unique_labels)
    cbar2.set_ticklabels(unique_labels)

    # Adjust layout for better spacing
    plt.tight_layout()

    fig.savefig(f"{folder}/latent_rep_at_{rnd}.png")
    plt.close()
    return f"{folder}/latent_rep_at_{rnd}.png"


def sample(net, device):
    """Generates samples usingfrom sklearn.mixture import GaussianMixture
    the decoder of the trained VAE."""
    with torch.no_grad():
        z = torch.randn(10)
        z = z.to(device)
        gen_image = net.decode(z)
    return gen_image


def denormalize(tensor):
    # Adjust the normalization to be the inverse of what was applied to your dataset
    return tensor * 0.5 + 0.5


def generate(net, image):
    """Reproduce the input with trained VAE."""
    with torch.no_grad():
        return net.forward(image)


if __name__ == "__main__":
    subset_alignment_dataloader(100, 100 * 4)
