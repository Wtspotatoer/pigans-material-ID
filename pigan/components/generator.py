from pathlib import Path
import numpy as np
import tensorflow as tf
from tensorflow.keras import layers



class Generator():
    """
    A class that encapsulates the concept of a generator that learns the 
    underlying distribution of some data, and is able to generate samples based
    on that learned distribution. In this case, the distribution of some 
    variable u and some variable E that are related through some PDE.

    Attributes
    ----------
    generator_u : Sequential
        A Keras Sequential model to generate samples of u.
    generator_E : Sequential
        A Keras Sequential model to generate samples of E.
    gen_opt : Optimizer
        A Keras Optimizer to update the generator's weights.
    noise_sampler : NoiseSampler
        Instance of the NoiseSampler class to generate noise for the generator
         inputs.
    pde : PDE
        Instance of PDE class to enforce a PDE constraint (based on the dataset)
         on the generator loss.
    boundary_condition : BoundaryCondition
        Instance of BoundaryCondition class to enforce boundary conditions in
         generator loss.

    Methods
    -------
    step(inputs, discriminator, batch_size)
        Generator training step after which the generator's weights are updated.
    _loss(fake_output, tape, **terms)
        Calculates the loss for the generator at a certain training step.
    _model(input_shape, dimensionality)
        Generates a Keras Sequential model.
    generate(X, num_samples, save_dir=None)
        Generates samples using the network's generator models.
    save(save_dir)
        Saves the generators' models.
    """

    def __init__(self, input_shape, pde, boundary_conditions,
                 optimizer, noise_sampler):
        """
        Parameters
        ----------
        input_shape : tuple
            Indicates the shape of the input the generator models will take. 
        dimensionality : int
            Number of output dimensions for the generator_u model.
        pde : PDE
            Instance of PDE class to enforce a PDE constraint (based on the 
            dataset) on the generator loss.
        boundary_condition : BoundaryCondition
            Instance of BoundaryCondition class to enforce boundary conditions 
            in generator loss.
        optimizer : Optimizer
            A Keras Optimizer to update the discriminator's weights.
        noise_sampler : NoiseSampler
            Instance of the NoiseSampler class to generate noise for the 
            generator inputs.
        """
        self.input_shape = input_shape

        self.generator_u = self._model(input_shape, 2)
        self.generator_E = self._model(input_shape, 1)

        self.gen_opt = optimizer
        self.noise_sampler = noise_sampler
        self.pde = pde
        self.boundary_conditions = boundary_conditions

    def __call__(self, gen_input, noise):
        u_pred = self.generator_u(tf.concat([gen_input, noise], axis=1))
        E_pred = self.generator_E(tf.concat([gen_input, noise], axis=1))

        return u_pred, E_pred

    @tf.function
    def step(self, inputs, discriminator, batch_size):
        """Generator training step.

        Parameters
        ----------
        inputs : dict
            Dictionary containing inputs for the generator.
        discriminator : Discriminator
            Instance of Discriminator class. Used to evaluate generated samples 
            and calculate loss.
        batch_size : tf.Tensor
            Number of samples in the current batch.

        Returns
        -------
        gen_loss : tf.Tensor
            Generator loss at the current step.
        pde_loss : tf.Tensor
            PDE constraint evaluation.
        """
        num_pde = 100

        with tf.GradientTape(persistent=True) as gen_tape:
            X_u = inputs['X_u']
            X_f = inputs['X_f']

            X_u_g = tf.tile(X_u, [batch_size, 1])

            noise_u = self.noise_sampler.sample_noise(X_u.shape[0], batch_size)
            noise_f = self.noise_sampler.sample_noise(X_f.shape[0], num_pde)

            u_inputs = tf.concat([X_u_g, noise_u], axis=1)
            generated_u = self.generator_u(u_inputs, training=True)
            generated_snapshots = tf.reshape(generated_u, [batch_size, -1])


            X_f_g = tf.tile(X_f, [num_pde, 1])
            gen_tape.watch(X_f_g)
            u_f_inputs = tf.concat([X_f_g, noise_f], axis=1)
            generated_f_u = self.generator_u(u_f_inputs, training=True)
            E_f_inputs = tf.concat([X_f_g, noise_f], axis=1)
            generated_f_E = self.generator_E(E_f_inputs , training=True)

            fake_output = discriminator.discriminator(generated_snapshots,
                                                      training=True)

            gen_loss, pde_loss, bc_loss = self._loss(fake_output, gen_tape,
                                                     X=X_f_g, u=generated_f_u,
                                                     E=generated_f_E)

            total_loss = gen_loss + pde_loss + bc_loss

        gradients_of_generators = gen_tape.gradient(total_loss,
                                                    [self.generator_u.trainable_variables,
                                                     self.generator_E.trainable_variables])

        self.gen_opt.apply_gradients(zip(gradients_of_generators[0],
                                         self.generator_u.trainable_variables))
        self.gen_opt.apply_gradients(zip(gradients_of_generators[1],
                                         self.generator_E.trainable_variables))

        del gen_tape

        return gen_loss, pde_loss, bc_loss

    def _loss(self, fake_output, tape, **terms):
        """Calculates the loss for the generator based on Equation 2 from 
        "Improved Training of Wasserstein GANs" by Gulrajani et al. and applies
         a PDE constraint to this loss.

        Parameters
        ----------
        fake_output : tf.Tensor
            Evaluation of the snapshots from the generator by the discriminator.
        tape : tf.GradientTape
            Gradient tape instance that has kept watch of the generator inputs.
        terms : dict
            Dictionary containing variables that will be used to evaluate the
            PDE constraint.
            
        Returns
        -------
        gen_loss : tf.Tensor
            Calculated generator loss.
        pde_loss : tf.Tensor
            PDE constraint evaluation.
        """
        wgan_gen_loss = -tf.reduce_mean(fake_output)
        pde_loss = self.pde.evaluate_loss(terms, tape)
        bc_loss = self.boundary_conditions.evaluate_loss(self.generator_u,
                                                    self.generator_E, tape)
        return wgan_gen_loss, pde_loss, bc_loss

    def _model(self, input_shape, dimensionality):
        """Creates a Sequential model (linear stack of layers).

        Parameters
        ----------
        input_shape : tuple
            Indicates the shape of the input the model will take. 
        dimensionality : int
            Number of output dimensions.

        Returns
        -------
        model : Sequential
            Keras Sequential model.  
        """
        model = tf.keras.Sequential()
        model.add(layers.Input(shape=input_shape))
        model.add(layers.Flatten())
        model.add(layers.Dense(128, kernel_initializer='glorot_uniform',
                               bias_initializer='zeros'))
        model.add(layers.Activation('tanh'))

        model.add(layers.Dense(128))
        model.add(layers.Activation('tanh'))

        model.add(layers.Dense(128))
        model.add(layers.Activation('tanh'))

        model.add(layers.Dense(128))
        model.add(layers.Activation('tanh'))

        model.add(layers.Dense(dimensionality))
        
        
        return model

    def generate(self, X, num_samples):
        """Generates a number of samples for both the u and E variables.

        Parameters
        ----------
        X : array_like 
            Array of points/coordinates with shape [number of points/coordinates, dimensionality], e.g., [10, 1] -> 1D, [10, 2] -> 2D 
        num_smaples : int
            Number of samples to generate.
        save_dir : string
            Path to the directory where the results will be saved.

        Returns
        -------
        u_samples : array
            An array of u samples with shape: [num_samples, number of 
            points/coordinates, dimensionality]
        E_samples : array
            An array of E samples with shape: [num_samples, number of 
            points/coordinates, 1]    
        """

        noise = self.noise_sampler.sample_noise(X.shape[0], num_samples)

        X_g = tf.tile(X, [num_samples, 1])
        
        u_inputs = tf.concat([X_g, noise], axis=1)
        generated_u = self.generator_u(u_inputs, training=False)
        generated_u = tf.reshape(generated_u,
                                 [num_samples, X.shape[0], -1])

        E_inputs = tf.concat([X_g, noise], axis=1)
        generated_E = self.generator_E(E_inputs,training=False)
                                                       
        generated_E = tf.reshape(generated_E,[num_samples, X.shape[0], -1])

        return generated_u, generated_E
    

    def save(self, save_dir):
        """Saves the generator models (architecture + weights) into hdf5 files using the save function from the Keras API.

        Parameters
        ----------
        save_dir : string
            Path to the directory where the models will be saved.
        """
        self.generator_u.save(save_dir.joinpath('generator_u.h5'))
        self.generator_E.save(save_dir.joinpath('generator_E.h5'))

    def load(self, model_dir):
        """Loads the generator models (architecture + weights) from hdf5 files using the load_model function from the Keras API.

        Parameters
        ----------
        load_dir : string
            Path to the directory where the models are saved.
        """
        self.generator_u = tf.keras.models.load_model(
                model_dir.joinpath('generator_u.h5'), compile=False)
        self.generator_E = tf.keras.models.load_model(
                model_dir.joinpath('generator_E.h5'), compile=False)

